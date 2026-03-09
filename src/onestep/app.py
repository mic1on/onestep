from __future__ import annotations

import asyncio
import contextlib
import importlib
import inspect
import logging
import signal
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from .connectors.base import Sink, Source
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

    async def wait_for_shutdown(self) -> None:
        shutdown = self._ensure_shutdown_event()
        await shutdown.wait()

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
        self._shutdown = asyncio.Event()
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
        if hook_error is not None:
            raise hook_error
        if close_error is not None:
            raise close_error

    async def serve(self) -> None:
        await self.startup()
        runners = [TaskRunner(self, task) for task in self._tasks if task.source is not None]
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
