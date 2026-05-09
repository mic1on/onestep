from __future__ import annotations

import copy
import inspect
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .connectors.base import Sink, Source
from .retry import NoRetry, RetryPolicy

TaskHandler = Callable[["TaskContext", Any], Any]
TaskHook = Callable[..., Any]


@dataclass(frozen=True)
class TaskHooks:
    before: tuple[TaskHook, ...] = ()
    after_success: tuple[TaskHook, ...] = ()
    on_failure: tuple[TaskHook, ...] = ()


@dataclass
class TaskSpec:
    name: str
    description: str | None
    handler: TaskHandler
    handler_ref: str | None
    source: Source | None
    sinks: tuple[Sink, ...]
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
        sinks: Sink | Sequence[Sink] | None,
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
        resolved_sinks = _normalize_sinks(sinks)
        resolved_dead_letter_sinks = _normalize_sinks(dead_letter)
        return cls(
            name=name,
            description=_normalize_description(description) or inspect.getdoc(handler),
            handler=handler,
            handler_ref=handler_ref,
            source=source,
            sinks=resolved_sinks,
            dead_letter_sinks=resolved_dead_letter_sinks,
            config=copy.deepcopy(dict(config or {})),
            metadata=copy.deepcopy(dict(metadata or {})),
            hooks=hooks or TaskHooks(),
            concurrency=concurrency,
            retry=retry or NoRetry(),
            timeout_s=timeout_s,
        )


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
