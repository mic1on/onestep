from __future__ import annotations

import asyncio
import contextlib
import importlib
import inspect
import logging
import signal
import copy
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from .connectors.base import Sink, Source
from .envelope import Envelope
from .events import TaskEvent
from .retry import RetryPolicy
from .runtime.runner import TaskRunner
from .state import InMemoryStateStore, StateStore
from .task import TaskHandler, TaskSpec


class OneStepApp:
    def __init__(
        self,
        name: str,
        *,
        config: Mapping[str, Any] | None = None,
        state: StateStore | None = None,
        shutdown_timeout_s: float | None = 30.0,
    ) -> None:
        if shutdown_timeout_s is not None and shutdown_timeout_s <= 0:
            raise ValueError("shutdown_timeout_s must be > 0")
        self.name = name
        self.config = dict(config or {})
        self.state = state or InMemoryStateStore()
        self.shutdown_timeout_s = shutdown_timeout_s
        self._tasks: list[TaskSpec] = []
        self._shutdown: asyncio.Event | None = None
        self._shutdown_requested = False
        self._drain: asyncio.Event | None = None
        self._drain_requested = False
        self._restart_requested = False
        self._paused_tasks: set[str] = set()
        self._runner_state: asyncio.Event | None = None
        self._runners: list[TaskRunner] = []
        self._resources: list[Any] = []
        self._startup_hooks: list[Callable[..., Any]] = []
        self._shutdown_hooks: list[Callable[..., Any]] = []
        self._event_handlers: list[Callable[..., Any]] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._events_logger = logging.getLogger(f"onestep.{name}.events")

    @property
    def tasks(self) -> tuple[TaskSpec, ...]:
        return tuple(self._tasks)

    @property
    def is_stopping(self) -> bool:
        return self._shutdown_requested or (self._shutdown.is_set() if self._shutdown is not None else False)

    @property
    def is_draining(self) -> bool:
        return self._drain_requested and not self.is_stopping

    @property
    def restart_requested(self) -> bool:
        return self._restart_requested

    def is_task_paused(self, task_name: str) -> bool:
        return task_name in self._paused_tasks and not self.is_stopping

    def request_shutdown(self) -> None:
        self._shutdown_requested = True
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        if self._shutdown is None:
            return
        if self._loop is None or self._loop is current_loop:
            self._shutdown.set()
            return
        self._loop.call_soon_threadsafe(self._shutdown.set)

    def request_restart(self) -> None:
        self._restart_requested = True
        self.request_shutdown()

    def request_drain(self) -> None:
        self._drain_requested = True
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        drain = self._ensure_drain_event()
        if self._loop is None or self._loop is current_loop:
            drain.set()
        else:
            self._loop.call_soon_threadsafe(drain.set)
        self.notify_runner_state_changed()

    def request_task_pause(self, task_name: str) -> None:
        self._require_controllable_task(task_name)
        self._paused_tasks.add(task_name)
        self.notify_runner_state_changed()

    def request_task_resume(self, task_name: str) -> None:
        self._require_controllable_task(task_name)
        self._paused_tasks.discard(task_name)
        self.notify_runner_state_changed()

    async def replay_task_dead_letters(self, task_name: str, *, limit: int) -> dict[str, Any]:
        if limit < 1:
            raise ValueError("dead-letter replay limit must be >= 1")
        task = self._require_dead_letter_replay_task(task_name)
        assert task.source is not None
        assert isinstance(task.source, Sink)
        dead_letter_source = task.dead_letter_sinks[0]
        assert isinstance(dead_letter_source, Source)

        attempted_count = 0
        replayed_count = 0
        failed_count = 0

        for delivery in await dead_letter_source.fetch(limit):
            attempted_count += 1
            try:
                replay_envelope = self._build_dead_letter_replay_envelope(delivery.envelope)
            except Exception:
                failed_count += 1
                self._events_logger.exception(
                    "dead-letter replay payload was invalid",
                    extra={"task_name": task_name},
                )
                with contextlib.suppress(Exception):
                    await delivery.retry()
                continue

            try:
                await task.source.send(replay_envelope)
            except Exception:
                failed_count += 1
                self._events_logger.exception(
                    "dead-letter replay publish failed",
                    extra={"task_name": task_name},
                )
                with contextlib.suppress(Exception):
                    await delivery.retry()
                continue

            try:
                await delivery.ack()
            except Exception:
                failed_count += 1
                self._events_logger.exception(
                    "dead-letter replay ack failed after publish",
                    extra={"task_name": task_name},
                )
                continue

            replayed_count += 1

        if attempted_count == 0:
            completion = "complete"
        elif failed_count == 0:
            completion = "complete"
        elif replayed_count > 0:
            completion = "partial"
        else:
            completion = "failed"

        return {
            "operation": "replay_dead_letters",
            "task_name": task_name,
            "requested": True,
            "completion": completion,
            "requested_limit": limit,
            "attempted_count": attempted_count,
            "replayed_count": replayed_count,
            "failed_count": failed_count,
            "empty": attempted_count == 0,
        }

    async def wait_for_shutdown(self) -> None:
        shutdown = self._ensure_shutdown_event()
        await shutdown.wait()

    async def wait_for_drain_request(self) -> None:
        drain = self._ensure_drain_event()
        await drain.wait()

    async def wait_for_task_pause_request(self, task_name: str) -> None:
        while not self.is_task_paused(task_name) and not self.is_stopping:
            runner_state = self._ensure_runner_state_event()
            await runner_state.wait()
            runner_state.clear()

    async def wait_for_stop_fetching(self, task_name: str | None = None) -> None:
        shutdown_task = asyncio.create_task(self.wait_for_shutdown())
        drain_task = asyncio.create_task(self.wait_for_drain_request())
        waiters = {shutdown_task, drain_task}
        if task_name is not None:
            waiters.add(asyncio.create_task(self.wait_for_task_pause_request(task_name)))
        done, pending = await asyncio.wait(
            waiters,
            return_when=asyncio.FIRST_COMPLETED,
        )
        for pending_task in pending:
            pending_task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        await asyncio.gather(*done, return_exceptions=True)

    async def wait_for_drain(self) -> dict[str, Any]:
        while True:
            status = self.drain_status()
            if status["drained"]:
                return status
            runner_state = self._ensure_runner_state_event()
            await runner_state.wait()
            runner_state.clear()

    async def wait_for_task_pause(self, task_name: str) -> dict[str, Any]:
        while True:
            status = self.task_pause_status(task_name)
            if status["paused"]:
                return status
            runner_state = self._ensure_runner_state_event()
            await runner_state.wait()
            runner_state.clear()

    async def wait_for_task_resume(self, task_name: str) -> dict[str, Any]:
        while True:
            status = self.task_resume_status(task_name)
            if status["accepting_new_work"]:
                return status
            runner_state = self._ensure_runner_state_event()
            await runner_state.wait()
            runner_state.clear()

    def register_runners(self, runners: Sequence[TaskRunner]) -> None:
        self._runners = list(runners)
        self.notify_runner_state_changed()

    def notify_runner_state_changed(self) -> None:
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        runner_state = self._ensure_runner_state_event()
        if self._loop is None or self._loop is current_loop:
            runner_state.set()
            return
        self._loop.call_soon_threadsafe(runner_state.set)

    def drain_status(self) -> dict[str, Any]:
        inflight_task_count = sum(runner.inflight_count for runner in self._runners)
        fetching_runner_count = sum(1 for runner in self._runners if runner.is_fetching)
        parked_runner_count = sum(1 for runner in self._runners if runner.is_drain_parked)
        runner_count = len(self._runners)
        drained = (
            self._drain_requested
            and inflight_task_count == 0
            and fetching_runner_count == 0
            and parked_runner_count == runner_count
        )
        return {
            "operation": "drain",
            "requested": self._drain_requested,
            "completion": "complete" if drained else "in_progress",
            "drained": drained,
            "accepting_new_work": not self._drain_requested,
            "runner_count": runner_count,
            "parked_runner_count": parked_runner_count,
            "fetching_runner_count": fetching_runner_count,
            "inflight_task_count": inflight_task_count,
        }

    def task_pause_status(self, task_name: str) -> dict[str, Any]:
        status = self._task_runtime_status(task_name)
        paused = (
            status["pause_requested"]
            and status["inflight_task_count"] == 0
            and status["fetching_runner_count"] == 0
            and status["parked_runner_count"] == status["runner_count"]
        )
        return {
            "operation": "pause_task",
            "task_name": task_name,
            "requested": status["pause_requested"],
            "completion": "complete" if paused else "in_progress",
            "paused": paused,
            "accepting_new_work": not status["pause_requested"],
            "runner_count": status["runner_count"],
            "parked_runner_count": status["parked_runner_count"],
            "fetching_runner_count": status["fetching_runner_count"],
            "inflight_task_count": status["inflight_task_count"],
        }

    def task_control_snapshot(self, task_name: str) -> dict[str, Any]:
        task = next((task for task in self._tasks if task.name == task_name), None)
        if task is None:
            raise ValueError(f"task {task_name} was not found")
        if task.source is None:
            raise ValueError(f"task {task_name} does not have a controllable source runner")

        runners = [runner for runner in self._runners if runner.task.name == task_name]
        pause_requested = self.is_task_paused(task_name)
        fetching_runner_count = sum(1 for runner in runners if runner.is_fetching)
        parked_runner_count = sum(1 for runner in runners if runner.is_pause_parked)
        inflight_task_count = sum(runner.inflight_count for runner in runners)
        runner_count = len(runners)
        paused = (
            pause_requested
            and inflight_task_count == 0
            and fetching_runner_count == 0
            and parked_runner_count == runner_count
        )
        return {
            "task_name": task_name,
            "supported_commands": self.task_supported_commands(task_name),
            "pause_requested": pause_requested,
            "paused": paused,
            "accepting_new_work": not pause_requested,
            "runner_count": runner_count,
            "parked_runner_count": parked_runner_count,
            "fetching_runner_count": fetching_runner_count,
            "inflight_task_count": inflight_task_count,
        }

    def task_control_snapshots(self) -> list[dict[str, Any]]:
        return [
            self.task_control_snapshot(task.name)
            for task in self._tasks
            if task.source is not None
        ]

    def task_supported_commands(self, task_name: str) -> list[str]:
        task = self._require_controllable_task(task_name)
        supported_commands = ["pause_task", "resume_task"]
        if self._task_supports_dead_letter_replay(task):
            supported_commands.append("replay_dead_letters")
        return supported_commands

    def supports_dead_letter_replay_commands(self) -> bool:
        return any(self._task_supports_dead_letter_replay(task) for task in self._tasks)

    def task_resume_status(self, task_name: str) -> dict[str, Any]:
        status = self._task_runtime_status(task_name)
        accepting_new_work = (
            not status["pause_requested"]
            and status["parked_runner_count"] == 0
        )
        return {
            "operation": "resume_task",
            "task_name": task_name,
            "requested": True,
            "completion": "complete" if accepting_new_work else "in_progress",
            "paused": not accepting_new_work,
            "accepting_new_work": accepting_new_work,
            "runner_count": status["runner_count"],
            "parked_runner_count": status["parked_runner_count"],
            "fetching_runner_count": status["fetching_runner_count"],
            "inflight_task_count": status["inflight_task_count"],
        }

    def on_startup(self, func: Callable[..., Any] | None = None):
        return self._register_hook(self._startup_hooks, func)

    def on_shutdown(self, func: Callable[..., Any] | None = None):
        return self._register_hook(self._shutdown_hooks, func)

    def on_event(self, func: Callable[..., Any] | None = None):
        return self._register_hook(self._event_handlers, func)

    def task(
        self,
        *,
        name: str | None = None,
        description: str | None = None,
        source: Source | None = None,
        emit: Sink | Sequence[Sink] | None = None,
        dead_letter: Sink | Sequence[Sink] | None = None,
        concurrency: int = 1,
        retry: RetryPolicy | None = None,
        timeout_s: float | None = None,
    ):
        def decorator(func: TaskHandler) -> TaskHandler:
            task = TaskSpec.build(
                name=name or func.__name__,
                description=description,
                handler=func,
                source=source,
                sinks=emit,
                dead_letter=dead_letter,
                concurrency=concurrency,
                retry=retry,
                timeout_s=timeout_s,
            )
            self._tasks.append(task)
            return func

        return decorator

    async def startup(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._shutdown_requested = False
        self._drain_requested = False
        self._restart_requested = False
        self._paused_tasks = set()
        self._shutdown = asyncio.Event()
        self._drain = asyncio.Event()
        self._runner_state = asyncio.Event()
        self._runners = []
        resources: list[Any] = []
        seen: set[int] = set()
        for task in self._tasks:
            if task.source is not None and id(task.source) not in seen:
                resources.append(task.source)
                seen.add(id(task.source))
            for sink in task.sinks:
                if id(sink) not in seen:
                    resources.append(sink)
                    seen.add(id(sink))
            for sink in task.dead_letter_sinks:
                if id(sink) not in seen:
                    resources.append(sink)
                    seen.add(id(sink))
        opened: list[Any] = []
        self._resources = []
        try:
            for resource in resources:
                await resource.open()
                opened.append(resource)
            self._resources = list(opened)
            await self._run_hooks(self._startup_hooks)
        except Exception:
            await self._close_resources(opened, suppress_exceptions=True)
            self._resources = []
            raise

    async def shutdown(self) -> None:
        self._shutdown_requested = True
        if self._shutdown is not None:
            self._shutdown.set()
        hook_error: BaseException | None = None
        try:
            await self._run_hooks(self._shutdown_hooks)
        except BaseException as exc:
            hook_error = exc
        finally:
            close_error = await self._close_resources(self._resources, suppress_exceptions=False)
            self._resources = []
            self._runners = []
            self._paused_tasks = set()
            self.notify_runner_state_changed()
        if hook_error is not None:
            raise hook_error
        if close_error is not None:
            raise close_error

    async def serve(self) -> None:
        await self.startup()
        runners = [TaskRunner(self, task) for task in self._tasks if task.source is not None]
        self.register_runners(runners)
        try:
            if not runners:
                return
            await asyncio.gather(*(runner.run() for runner in runners))
        finally:
            await self.shutdown()

    def run(self) -> None:
        try:
            with self._install_signal_handlers():
                asyncio.run(self.serve())
        except KeyboardInterrupt:
            return None

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "shutdown_timeout_s": self.shutdown_timeout_s,
            "tasks": [
                {
                    "name": task.name,
                    "description": task.description,
                    "source": _describe_resource(task.source),
                    "emit": [_describe_resource(sink) for sink in task.sinks],
                    "dead_letter": [_describe_resource(sink) for sink in task.dead_letter_sinks],
                    "concurrency": task.concurrency,
                    "timeout_s": task.timeout_s,
                    "retry": task.retry.__class__.__name__,
                }
                for task in self._tasks
            ],
        }

    @classmethod
    def load(cls, target: str) -> "OneStepApp":
        from .config import is_yaml_target, load_yaml_app

        if is_yaml_target(target):
            return load_yaml_app(target)
        module_name, _, attr = target.partition(":")
        app_attr = attr or "app"
        module = importlib.import_module(module_name)
        value = getattr(module, app_attr)
        if isinstance(value, cls):
            return value
        if callable(value):
            resolved = _invoke_app_factory(target, value)
            if isinstance(resolved, cls):
                return resolved
        raise TypeError(f"{target} did not resolve to OneStepApp or a zero-argument factory")

    def _register_hook(
        self,
        storage: list[Callable[..., Any]],
        func: Callable[..., Any] | None,
    ):
        def decorator(callback: Callable[..., Any]) -> Callable[..., Any]:
            storage.append(callback)
            return callback

        if func is None:
            return decorator
        return decorator(func)

    async def _run_hooks(self, hooks: Sequence[Callable[..., Any]]) -> None:
        for hook in hooks:
            result = _invoke_hook(hook, self)
            if inspect.isawaitable(result):
                await result

    async def emit_event(self, event: TaskEvent) -> None:
        for handler in self._event_handlers:
            try:
                result = _invoke_single_arg(handler, event)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                self._events_logger.exception("event handler failed", extra={"event_kind": event.kind.value})

    async def _close_resources(
        self,
        resources: Sequence[Any],
        *,
        suppress_exceptions: bool,
    ) -> BaseException | None:
        first_error: BaseException | None = None
        for resource in reversed(resources):
            try:
                await resource.close()
            except BaseException as exc:
                if first_error is None:
                    first_error = exc
                self._events_logger.exception(
                    "resource close failed",
                    extra={"resource_name": getattr(resource, "name", resource.__class__.__name__)},
                )
        if first_error is not None and not suppress_exceptions:
            return first_error
        return None

    def _ensure_shutdown_event(self) -> asyncio.Event:
        current_loop = asyncio.get_running_loop()
        if self._shutdown is None or self._loop is not current_loop:
            self._loop = current_loop
            self._shutdown = asyncio.Event()
            if self._shutdown_requested:
                self._shutdown.set()
        return self._shutdown

    def _ensure_drain_event(self) -> asyncio.Event:
        current_loop = asyncio.get_running_loop()
        if self._drain is None or self._loop is not current_loop:
            self._loop = current_loop
            self._drain = asyncio.Event()
            if self._drain_requested:
                self._drain.set()
        return self._drain

    def _require_controllable_task(self, task_name: str) -> TaskSpec:
        task = next((task for task in self._tasks if task.name == task_name), None)
        if task is None:
            raise ValueError(f"task {task_name} was not found")
        if task.source is None:
            raise ValueError(f"task {task_name} does not have a controllable source runner")
        return task

    def _require_task_runners(self, task_name: str) -> list[TaskRunner]:
        self._require_controllable_task(task_name)
        return [runner for runner in self._runners if runner.task.name == task_name]

    def _require_dead_letter_replay_task(self, task_name: str) -> TaskSpec:
        task = self._require_controllable_task(task_name)
        if not self._task_supports_dead_letter_replay(task):
            raise ValueError(
                f"task {task_name} does not support dead-letter replay with the configured source and dead-letter connectors"
            )
        return task

    def _task_supports_dead_letter_replay(self, task: TaskSpec) -> bool:
        return (
            task.source is not None
            and isinstance(task.source, Sink)
            and len(task.dead_letter_sinks) == 1
            and isinstance(task.dead_letter_sinks[0], Source)
        )

    def _build_dead_letter_replay_envelope(self, dead_letter_envelope: Envelope) -> Envelope:
        if not isinstance(dead_letter_envelope.body, Mapping):
            raise ValueError("dead-letter envelope body must be a mapping")
        if "payload" not in dead_letter_envelope.body:
            raise ValueError("dead-letter envelope body is missing payload")

        original_payload = copy.deepcopy(dead_letter_envelope.body["payload"])
        original_meta = dead_letter_envelope.meta.get("original_meta")
        if not isinstance(original_meta, Mapping):
            replay_meta: dict[str, Any] = {}
        else:
            replay_meta = copy.deepcopy(dict(original_meta))

        original_attempts = dead_letter_envelope.meta.get("original_attempts")
        replay_attempts = original_attempts if isinstance(original_attempts, int) and original_attempts >= 0 else 0
        return Envelope(
            body=original_payload,
            meta=replay_meta,
            attempts=replay_attempts,
        )

    def _task_runtime_status(self, task_name: str) -> dict[str, Any]:
        runners = self._require_task_runners(task_name)
        return {
            "pause_requested": self.is_task_paused(task_name),
            "runner_count": len(runners),
            "parked_runner_count": sum(1 for runner in runners if runner.is_pause_parked),
            "fetching_runner_count": sum(1 for runner in runners if runner.is_fetching),
            "inflight_task_count": sum(runner.inflight_count for runner in runners),
        }

    def _ensure_runner_state_event(self) -> asyncio.Event:
        current_loop = asyncio.get_running_loop()
        if self._runner_state is None or self._loop is not current_loop:
            self._loop = current_loop
            self._runner_state = asyncio.Event()
        return self._runner_state

    @contextlib.contextmanager
    def _install_signal_handlers(self):
        installed: list[tuple[int, Any]] = []

        def handle_signal(signum, frame) -> None:
            self.request_shutdown()

        for sig_name in ("SIGINT", "SIGTERM"):
            sig = getattr(signal, sig_name, None)
            if sig is None:
                continue
            try:
                previous = signal.getsignal(sig)
                signal.signal(sig, handle_signal)
            except (ValueError, OSError):
                continue
            installed.append((sig, previous))
        try:
            yield
        finally:
            for sig, previous in reversed(installed):
                with contextlib.suppress(ValueError, OSError):
                    signal.signal(sig, previous)


def _invoke_hook(hook: Callable[..., Any], app: OneStepApp) -> Any:
    try:
        signature = inspect.signature(hook)
    except (TypeError, ValueError):
        return hook(app)

    positional = [
        parameter
        for parameter in signature.parameters.values()
        if parameter.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    has_varargs = any(parameter.kind is inspect.Parameter.VAR_POSITIONAL for parameter in signature.parameters.values())
    if not positional and not has_varargs:
        return hook()
    return hook(app)


def _invoke_single_arg(callback: Callable[..., Any], value: Any) -> Any:
    try:
        signature = inspect.signature(callback)
    except (TypeError, ValueError):
        return callback(value)

    positional = [
        parameter
        for parameter in signature.parameters.values()
        if parameter.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    has_varargs = any(parameter.kind is inspect.Parameter.VAR_POSITIONAL for parameter in signature.parameters.values())
    if not positional and not has_varargs:
        return callback()
    return callback(value)


def _describe_resource(resource: Source | Sink | None) -> dict[str, str] | None:
    if resource is None:
        return None
    return {
        "name": resource.name,
        "type": resource.__class__.__name__,
    }


def _invoke_app_factory(target: str, factory: Callable[..., Any]) -> Any:
    try:
        signature = inspect.signature(factory)
    except (TypeError, ValueError):
        return factory()

    required_positional = [
        parameter
        for parameter in signature.parameters.values()
        if parameter.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        and parameter.default is inspect._empty
    ]
    required_keyword_only = [
        parameter
        for parameter in signature.parameters.values()
        if parameter.kind is inspect.Parameter.KEYWORD_ONLY and parameter.default is inspect._empty
    ]
    if required_positional or required_keyword_only:
        raise TypeError(f"{target} factory must not require arguments")
    return factory()
