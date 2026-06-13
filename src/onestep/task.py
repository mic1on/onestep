from __future__ import annotations

import copy
import inspect
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Union

from .connectors.base import Sink, Source
from .retry import NoRetry, RetryPolicy

TaskHandler = Callable[["TaskContext", Any], Any]
TaskHook = Callable[..., Any]
TaskPredicate = Callable[..., Any]


@dataclass(frozen=True)
class TaskHooks:
    before: tuple[TaskHook, ...] = ()
    after_success: tuple[TaskHook, ...] = ()
    on_failure: tuple[TaskHook, ...] = ()


@dataclass(frozen=True)
class EmitRoute:
    then_sinks: tuple[Sink, ...]
    predicate: TaskPredicate | None = None
    otherwise_sinks: tuple[Sink, ...] = ()
    predicate_ref: str | None = None

    @property
    def sinks(self) -> tuple[Sink, ...]:
        return (*self.then_sinks, *self.otherwise_sinks)


EmitTarget = Union[Sink, EmitRoute]


@dataclass
class TaskSpec:
    name: str
    description: str | None
    handler: TaskHandler
    handler_ref: str | None
    source: Source | None
    sinks: tuple[Sink, ...]
    emit_routes: tuple[EmitRoute, ...]
    dead_letter_sinks: tuple[Sink, ...]
    config: dict[str, Any]
    metadata: dict[str, Any]
    hooks: TaskHooks
    concurrency: int
    retry: RetryPolicy
    timeout_s: float | None

    @classmethod
    def build(
        cls,
        *,
        name: str,
        description: str | None,
        handler: TaskHandler,
        handler_ref: str | None,
        source: Source | None,
        sinks: EmitTarget | Sequence[EmitTarget] | None,
        dead_letter: Sink | Sequence[Sink] | None,
        config: Mapping[str, Any] | None,
        metadata: Mapping[str, Any] | None,
        hooks: TaskHooks | None,
        concurrency: int,
        retry: RetryPolicy | None,
        timeout_s: float | None,
    ) -> "TaskSpec":
        if concurrency < 1:
            raise ValueError("concurrency must be >= 1")
        if timeout_s is not None and timeout_s <= 0:
            raise ValueError("timeout_s must be > 0")
        resolved_emit_routes = _normalize_emit_routes(sinks)
        resolved_dead_letter_sinks = _normalize_sinks(dead_letter)
        return cls(
            name=name,
            description=_normalize_description(description) or inspect.getdoc(handler),
            handler=handler,
            handler_ref=handler_ref,
            source=source,
            sinks=_flatten_emit_route_sinks(resolved_emit_routes),
            emit_routes=resolved_emit_routes,
            dead_letter_sinks=resolved_dead_letter_sinks,
            config=copy.deepcopy(dict(config or {})),
            metadata=copy.deepcopy(dict(metadata or {})),
            hooks=hooks or TaskHooks(),
            concurrency=concurrency,
            retry=retry or NoRetry(),
            timeout_s=timeout_s,
        )


def _normalize_emit_routes(targets: EmitTarget | Sequence[EmitTarget] | None) -> tuple[EmitRoute, ...]:
    if targets is None:
        return ()
    if isinstance(targets, Sequence) and not isinstance(targets, (str, bytes)):
        entries = tuple(targets)
    else:
        entries = (targets,)

    routes: list[EmitRoute] = []
    for entry in entries:
        if isinstance(entry, EmitRoute):
            routes.append(entry)
        elif isinstance(entry, Sink):
            routes.append(EmitRoute(then_sinks=(entry,)))
        else:
            raise TypeError("emit entries must be Sink or EmitRoute instances")
    return tuple(routes)


def _flatten_emit_route_sinks(routes: Iterable[EmitRoute]) -> tuple[Sink, ...]:
    sinks: list[Sink] = []
    for route in routes:
        sinks.extend(route.sinks)
    return tuple(sinks)


def _normalize_sinks(sinks: Sink | Sequence[Sink] | None) -> tuple[Sink, ...]:
    if sinks is None:
        return ()
    if isinstance(sinks, Sequence) and not isinstance(sinks, (str, bytes)):
        return tuple(sinks)
    return (sinks,)


def _normalize_description(description: str | None) -> str | None:
    if description is None:
        return None
    normalized = description.strip()
    return normalized or None
