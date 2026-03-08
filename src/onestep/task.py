from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from .connectors.base import Sink, Source
from .retry import NoRetry, RetryPolicy

TaskHandler = Callable[["TaskContext", Any], Any]


@dataclass
class TaskSpec:
    name: str
    handler: TaskHandler
    source: Source | None
    sinks: tuple[Sink, ...]
    dead_letter_sinks: tuple[Sink, ...]
    concurrency: int
    retry: RetryPolicy
    timeout_s: float | None

    @classmethod
    def build(
        cls,
        *,
        name: str,
        handler: TaskHandler,
        source: Source | None,
        sinks: Sink | Sequence[Sink] | None,
        dead_letter: Sink | Sequence[Sink] | None,
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
            handler=handler,
            source=source,
            sinks=resolved_sinks,
            dead_letter_sinks=resolved_dead_letter_sinks,
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
