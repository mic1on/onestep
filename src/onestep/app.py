from __future__ import annotations

import copy
import importlib
import inspect
import logging
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from .capture.config import FailureCaptureConfig
from .capture.writer import FailureCaptureWriter
from .connectors.base import Sink, Source
from .events import StructuredEventLogger, TaskEvent
from .metrics import CustomMetricsRegistry
from .retry import RetryPolicy
from .runtime.event_hub import EventHub
from .runtime.lifecycle import LifecycleController
from .runtime.runner import TaskRunner
from .runtime.task_ops import TaskOperations
from .state import InMemoryStateStore, StateStore
from .task import EmitTarget, TaskHandler, TaskHooks, TaskSpec


class OneStepApp:
    """Facade for an onestep async task runtime.

    Construction, task/resource registration, ``describe``/``load``/``run``,
    and the event-hook surface live here. The asyncio lifecycle, the per-task
    runner registry, the control-plane state snapshots, the dead-letter /
    manual-run operations, and the event hub are delegated to
    ``LifecycleController``, ``TaskOperations``, and ``EventHub``. Public
    method names, signatures, and return structures are unchanged.
    """

    def __init__(
        self,
        name: str,
        *,
        config: Mapping[str, Any] | None = None,
        state: StateStore | None = None,
        shutdown_timeout_s: float | None = 30.0,
        failure_capture: FailureCaptureConfig | None = None,
    ) -> None:
        if shutdown_timeout_s is not None and shutdown_timeout_s <= 0:
            raise ValueError("shutdown_timeout_s must be > 0")
        self.name = name
        self.config = dict(config or {})
        self.state = state or InMemoryStateStore()
        self.shutdown_timeout_s = shutdown_timeout_s
        self.failure_capture = failure_capture
        self._failure_capture_writer = (
            FailureCaptureWriter(failure_capture)
            if failure_capture is not None
            else None
        )
        self.custom_metrics = CustomMetricsRegistry()
        self._tasks: list[TaskSpec] = []
        self._named_resources: dict[str, Any] = {}
        self._reporter_summary: dict[str, Any] | None = None
        self._events = EventHub(self)
        self._task_ops = TaskOperations(self)
        self._lifecycle = LifecycleController(self)

    # ----- task / resource registry (owned by the facade) -----------------

    @property
    def tasks(self) -> tuple[TaskSpec, ...]:
        return tuple(self._tasks)

    @property
    def resources(self) -> Mapping[str, Any]:
        return self._named_resources

    def bind_resources(self, resources: Mapping[str, Any]) -> None:
        self._named_resources = dict(resources)

    def register_resource(self, name: str, resource: Any) -> Any:
        self._named_resources[name] = resource
        return resource

    def set_reporter_summary(self, reporter: Mapping[str, Any] | None) -> None:
        self._reporter_summary = None if reporter is None else copy.deepcopy(dict(reporter))

    # ----- lifecycle state (delegated to LifecycleController) -------------

    @property
    def is_stopping(self) -> bool:
        return self._lifecycle.is_stopping

    @property
    def is_draining(self) -> bool:
        return self._lifecycle.is_draining

    @property
    def restart_requested(self) -> bool:
        return self._lifecycle.restart_requested

    def is_task_paused(self, task_name: str) -> bool:
        return self._lifecycle.is_task_paused(task_name)

    def request_shutdown(self) -> None:
        self._lifecycle.request_shutdown()

    def request_restart(self) -> None:
        self._lifecycle.request_restart()

    def request_drain(self) -> None:
        self._lifecycle.request_drain()

    def request_task_pause(self, task_name: str) -> None:
        self._lifecycle.request_task_pause(task_name)

    def request_task_resume(self, task_name: str) -> None:
        self._lifecycle.request_task_resume(task_name)

    # ----- task operations (delegated to TaskOperations) ------------------

    async def replay_task_dead_letters(self, task_name: str, *, limit: int) -> dict[str, Any]:
        return await self._task_ops.replay_task_dead_letters(task_name, limit=limit)

    async def discard_task_dead_letters(self, task_name: str, *, limit: int) -> dict[str, Any]:
        return await self._task_ops.discard_task_dead_letters(task_name, limit=limit)

    async def run_task_once(
        self,
        task_name: str,
        *,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        return await self._task_ops.run_task_once(task_name, payload=payload)

    def supports_dead_letter_replay_commands(self) -> bool:
        return self._task_ops.supports_dead_letter_replay_commands()

    def supports_dead_letter_discard_commands(self) -> bool:
        return self._task_ops.supports_dead_letter_discard_commands()

    def supports_manual_run_commands(self) -> bool:
        return self._task_ops.supports_manual_run_commands()

    # ----- waiters (delegated to LifecycleController) ---------------------

    async def wait_for_shutdown(self) -> None:
        await self._lifecycle.wait_for_shutdown()

    async def wait_for_drain_request(self) -> None:
        await self._lifecycle.wait_for_drain_request()

    async def wait_for_task_pause_request(self, task_name: str) -> None:
        await self._lifecycle.wait_for_task_pause_request(task_name)

    async def wait_for_stop_fetching(self, task_name: str | None = None) -> None:
        await self._lifecycle.wait_for_stop_fetching(task_name)

    async def wait_for_drain(self) -> dict[str, Any]:
        return await self._lifecycle.wait_for_drain()

    async def wait_for_task_pause(self, task_name: str) -> dict[str, Any]:
        return await self._lifecycle.wait_for_task_pause(task_name)

    async def wait_for_task_resume(self, task_name: str) -> dict[str, Any]:
        return await self._lifecycle.wait_for_task_resume(task_name)

    # ----- runner registry (delegated to LifecycleController) -------------

    def register_runners(self, runners: Sequence[TaskRunner]) -> None:
        self._lifecycle.register_runners(runners)

    async def stop_task_runner(self, task_name: str) -> dict[str, Any]:
        return await self._lifecycle.stop_task_runner(task_name)

    async def start_task_runner(self, task_name: str) -> dict[str, Any]:
        return await self._lifecycle.start_task_runner(task_name)

    async def restart_task_runner(self, task_name: str) -> dict[str, Any]:
        return await self._lifecycle.restart_task_runner(task_name)

    def notify_runner_state_changed(self) -> None:
        self._lifecycle.notify_runner_state_changed()

    # ----- control-plane snapshots (delegated to LifecycleController) -----

    def drain_status(self) -> dict[str, Any]:
        return self._lifecycle.drain_status()

    def task_pause_status(self, task_name: str) -> dict[str, Any]:
        return self._lifecycle.task_pause_status(task_name)

    def task_control_snapshot(self, task_name: str) -> dict[str, Any]:
        return self._lifecycle.task_control_snapshot(task_name)

    def task_control_snapshots(self) -> list[dict[str, Any]]:
        return self._lifecycle.task_control_snapshots()

    def task_supported_commands(self, task_name: str) -> list[str]:
        return self._lifecycle.task_supported_commands(task_name)

    def task_resume_status(self, task_name: str) -> dict[str, Any]:
        return self._lifecycle.task_resume_status(task_name)

    # ----- event hub (delegated to EventHub) ------------------------------

    @property
    def events_logger(self) -> logging.Logger:
        return self._events.events_logger

    def on_startup(self, func: Callable[..., Any] | None = None):
        return self._events.on_startup(func)

    def on_shutdown(self, func: Callable[..., Any] | None = None):
        return self._events.on_shutdown(func)

    def on_event(self, func: Callable[..., Any] | None = None):
        return self._events.on_event(func)

    def enable_structured_event_logging(self) -> StructuredEventLogger:
        return self._events.enable_structured_event_logging()

    # ----- task declaration ----------------------------------------------

    def task(
        self,
        *,
        name: str | None = None,
        description: str | None = None,
        source: Source | None = None,
        emit: EmitTarget | Sequence[EmitTarget] | None = None,
        dead_letter: Sink | Sequence[Sink] | None = None,
        config: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
        handler_ref: str | None = None,
        hooks: TaskHooks | None = None,
        concurrency: int = 1,
        retry: RetryPolicy | None = None,
        timeout_s: float | None = None,
    ):
        def decorator(func: TaskHandler) -> TaskHandler:
            task_name = name or func.__name__
            validate_task = getattr(source, "validate_task", None)
            if callable(validate_task):
                validate_task(task_name)
            task = TaskSpec.build(name=task_name, description=description, handler=func,
                handler_ref=handler_ref, source=source, sinks=emit, dead_letter=dead_letter,
                config=config, metadata=metadata, hooks=hooks, concurrency=concurrency,
                retry=retry, timeout_s=timeout_s)
            self._tasks.append(task)
            return func

        return decorator

    # ----- lifecycle phases (delegated to LifecycleController) ------------

    async def startup(self) -> None:
        await self._lifecycle.startup()

    async def shutdown(self) -> None:
        await self._lifecycle.shutdown()

    async def serve(self) -> None:
        await self._lifecycle.serve()

    def run(self) -> None:
        self._lifecycle.run()

    # ----- introspection / loading ---------------------------------------

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "shutdown_timeout_s": self.shutdown_timeout_s,
            "reporter": copy.deepcopy(self._reporter_summary),
            "resources": [
                {
                    "key": name,
                    "name": getattr(resource, "name", name),
                    "type": resource.__class__.__name__,
                }
                for name, resource in self._named_resources.items()
            ],
            "hooks": {
                "startup": self._events.startup_hook_count,
                "shutdown": self._events.shutdown_hook_count,
                "events": self._events.event_handler_count,
            },
            "tasks": [
                {
                    "name": task.name,
                    "description": task.description,
                    "handler_ref": task.handler_ref,
                    "source": _describe_resource(task.source),
                    "emit": [_describe_resource(sink) for sink in task.sinks],
                    "emit_bindings": [{"sink": _describe_resource(b.sink), "transform_ref": b.transform_ref} for b in task.emit_bindings],
                    "dead_letter": [_describe_resource(sink) for sink in task.dead_letter_sinks],
                    "config": copy.deepcopy(task.config),
                    "metadata": copy.deepcopy(task.metadata),
                    "hooks": {"before": len(task.hooks.before), "after_success": len(task.hooks.after_success), "on_failure": len(task.hooks.on_failure)},
                    "concurrency": task.concurrency,
                    "timeout_s": task.timeout_s,
                    "retry": task.retry.__class__.__name__,
                }
                for task in self._tasks
            ],
        }

    @classmethod
    def load(
        cls,
        target: str,
        *,
        env: Mapping[str, str] | None = None,
    ) -> "OneStepApp":
        from .config import is_yaml_target, load_yaml_app

        if is_yaml_target(target):
            return load_yaml_app(target, env=env)
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

    # ----- event hub internals (delegated to EventHub) --------------------

    def _register_hook(
        self,
        storage: list[Callable[..., Any]],
        func: Callable[..., Any] | None,
    ):
        return self._events._register_hook(storage, func)

    async def _run_hooks(self, hooks: Sequence[Callable[..., Any]]) -> None:
        await self._events.run_hooks(hooks)

    async def emit_event(self, event: TaskEvent) -> None:
        await self._events.emit_event(event)

    def _install_signal_handlers(self):
        return self._lifecycle._install_signal_handlers()

    # ----- contract-visible state (read-only views of the controller) -----

    @property
    def _runners(self) -> list[Any]:
        return self._lifecycle._runners

    @property
    def _runner_tasks(self) -> dict[str, Any]:
        return self._lifecycle._runner_tasks

    @property
    def _resources(self) -> list[Any]:
        return self._lifecycle._resources


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
