"""Hook registry and structured event dispatcher for :class:`OneStepApp`.

This module is a private implementation detail of :mod:`onestep.app`. It owns
the three hook lists and the structured event handlers that used to live
directly on ``OneStepApp``. Public behaviour is unchanged; the methods are
invoked through facade delegation on ``OneStepApp``.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable, Sequence
from typing import Any

from ..events import StructuredEventLogger, TaskEvent
from ..invoke import invoke_callback


class EventHub:
    """Owns startup / shutdown hooks and event handlers for an app instance.

    The hub receives the owning :class:`OneStepApp` once and reuses the
    reference to dispatch hooks (so that hook callbacks see the app as
    ``self``) and to log failures through the app's named events logger.
    """

    def __init__(self, app: Any) -> None:
        self._app = app
        self._startup_hooks: list[Callable[..., Any]] = []
        self._shutdown_hooks: list[Callable[..., Any]] = []
        self._event_handlers: list[Callable[..., Any]] = []
        self._events_logger = logging.getLogger(f"onestep.{app.name}.events")

    @property
    def startup_hook_count(self) -> int:
        return len(self._startup_hooks)

    @property
    def shutdown_hook_count(self) -> int:
        return len(self._shutdown_hooks)

    @property
    def event_handler_count(self) -> int:
        return len(self._event_handlers)

    @property
    def startup_hooks(self) -> list[Callable[..., Any]]:
        return self._startup_hooks

    @property
    def shutdown_hooks(self) -> list[Callable[..., Any]]:
        return self._shutdown_hooks

    @property
    def events_logger(self) -> logging.Logger:
        return self._events_logger

    @property
    def event_handlers(self) -> list[Callable[..., Any]]:
        return self._event_handlers

    def on_startup(self, func: Callable[..., Any] | None = None):
        return self._register_hook(self._startup_hooks, func)

    def on_shutdown(self, func: Callable[..., Any] | None = None):
        return self._register_hook(self._shutdown_hooks, func)

    def on_event(self, func: Callable[..., Any] | None = None):
        return self._register_hook(self._event_handlers, func)

    def enable_structured_event_logging(self) -> StructuredEventLogger:
        for handler in self._event_handlers:
            if isinstance(handler, StructuredEventLogger):
                return handler
        handler = StructuredEventLogger()
        self.on_event(handler)
        return handler

    async def run_hooks(self, hooks: Sequence[Callable[..., Any]]) -> None:
        for hook in hooks:
            result = invoke_callback(hook, self._app)
            if inspect.isawaitable(result):
                await result

    async def emit_event(self, event: TaskEvent) -> None:
        for handler in self._event_handlers:
            try:
                result = invoke_callback(handler, event)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                self._events_logger.exception(
                    "event handler failed", extra={"event_kind": event.kind.value}
                )

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
