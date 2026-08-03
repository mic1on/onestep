from __future__ import annotations

import inspect
import random
import traceback as traceback_module
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from .envelope import Envelope


class FailureKind(str, Enum):
    ERROR = "error"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class FailureInfo:
    kind: FailureKind
    exception_type: str
    message: str
    traceback: str | None = None

    @classmethod
    def from_exception(cls, exc: BaseException, *, kind: FailureKind) -> "FailureInfo":
        traceback_text = None
        if exc.__traceback__ is not None:
            traceback_text = "".join(
                traceback_module.format_exception(type(exc), exc, exc.__traceback__)
            )
        return cls(
            kind=kind,
            exception_type=type(exc).__name__,
            message=str(exc),
            traceback=traceback_text,
        )

    def as_dict(self) -> dict[str, str]:
        payload = {
            "kind": self.kind.value,
            "exception_type": self.exception_type,
            "message": self.message,
        }
        if self.traceback is not None:
            payload["traceback"] = self.traceback
        return payload


class RetryDecision(str, Enum):
    RETRY = "retry"
    FAIL = "fail"


@dataclass
class RetryAction:
    decision: RetryDecision
    delay_s: float | None = None


class RetryPolicy(Protocol):
    def on_error(self, envelope: Envelope, exc: Exception, failure: FailureInfo) -> RetryAction: ...


class NoRetry:
    def on_error(self, envelope: Envelope, exc: Exception, failure: FailureInfo) -> RetryAction:
        return RetryAction(RetryDecision.FAIL)


class MaxAttempts:
    def __init__(self, max_attempts: int = 3, delay_s: float | None = None) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        self.max_attempts = max_attempts
        self.delay_s = delay_s

    def on_error(self, envelope: Envelope, exc: Exception, failure: FailureInfo) -> RetryAction:
        next_attempt = envelope.attempts + 1
        if next_attempt < self.max_attempts:
            return RetryAction(RetryDecision.RETRY, delay_s=self.delay_s)
        return RetryAction(RetryDecision.FAIL)


class ExponentialBackoff:
    """Retry with exponential backoff and optional jitter.

    The delay before attempt *n* (0-indexed) is::

        delay = min(max_delay_s, min_delay_s * multiplier ** n)

    Jitter options:

    - ``"none"``: use the raw exponential delay.
    - ``"full"``: ``random.uniform(0, delay)`` — AWS SDK style.
    - ``"equal"``: ``random.uniform(delay / 2, delay * 1.5)`` — capped at
      ``max_delay_s``.
    """

    _JITTER_OPTIONS = frozenset({"none", "full", "equal"})

    def __init__(
        self,
        max_attempts: int = 3,
        min_delay_s: float = 1.0,
        max_delay_s: float = 60.0,
        multiplier: float = 2.0,
        jitter: str = "none",
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if min_delay_s < 0:
            raise ValueError("min_delay_s must be >= 0")
        if max_delay_s < min_delay_s:
            raise ValueError("max_delay_s must be >= min_delay_s")
        if multiplier <= 0:
            raise ValueError("multiplier must be > 0")
        if jitter not in self._JITTER_OPTIONS:
            raise ValueError(f"jitter must be one of {sorted(self._JITTER_OPTIONS)}")

        self.max_attempts = max_attempts
        self.min_delay_s = min_delay_s
        self.max_delay_s = max_delay_s
        self.multiplier = multiplier
        self.jitter = jitter

    def on_error(self, envelope: Envelope, exc: Exception, failure: FailureInfo) -> RetryAction:
        next_attempt = envelope.attempts + 1
        if next_attempt >= self.max_attempts:
            return RetryAction(RetryDecision.FAIL)

        delay = self.min_delay_s * (self.multiplier ** envelope.attempts)
        delay = min(delay, self.max_delay_s)

        if self.jitter == "full":
            delay = random.uniform(0, delay)
        elif self.jitter == "equal":
            half = delay / 2.0
            delay = random.uniform(max(0.0, delay - half), min(self.max_delay_s, delay + half))

        return RetryAction(RetryDecision.RETRY, delay_s=delay)


class ByFailureKind:
    """Route retry decisions to a sub-policy based on ``FailureKind``.

    Accepts a mapping of ``{failure_kind_value: RetryPolicy}`` plus an
    optional ``"default"`` key.  When the failure kind has no matching entry
    and no default is provided, the delivery is failed (``NoRetry``).
    """

    def __init__(self, **policies: RetryPolicy) -> None:
        if not policies:
            raise ValueError("at least one sub-policy is required")
        self._policies = policies

    def on_error(self, envelope: Envelope, exc: Exception, failure: FailureInfo) -> RetryAction:
        policy = self._policies.get(failure.kind.value) or self._policies.get("default")
        if policy is None:
            return RetryAction(RetryDecision.FAIL)
        return policy.on_error(envelope, exc, failure)


def resolve_retry_action(
    policy: RetryPolicy,
    envelope: Envelope,
    exc: Exception,
    failure: FailureInfo,
) -> RetryAction:
    on_error = policy.on_error
    try:
        signature = inspect.signature(on_error)
    except (TypeError, ValueError):
        return on_error(envelope, exc, failure)

    positional = [
        parameter
        for parameter in signature.parameters.values()
        if parameter.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    has_varargs = any(parameter.kind is inspect.Parameter.VAR_POSITIONAL for parameter in signature.parameters.values())
    if has_varargs or len(positional) >= 3:
        return on_error(envelope, exc, failure)
    return on_error(envelope, exc)
