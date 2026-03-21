from __future__ import annotations

import inspect
import traceback as traceback_module
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from .envelope import Envelope


class RetryInLocal(Exception):
    """Exception to trigger in-process retry without broker I/O.

    Unlike queue-based retry (which calls message.requeue() and creates
    a new broker message), RetryInLocal retries the handler directly
    within the same worker process, preserving the current delivery context
    and avoiding additional broker round-trips.

    For SQS FIFO queues, this also avoids the deduplication issue where
    retry messages with stable MessageDeduplicationId may be dropped.

    Example:
        async def my_handler(ctx, payload):
            try:
                return await unreliable_service_call(payload)
            except TransientError:
                raise RetryInLocal(delay_s=1.0)  # Retry locally with 1s delay
    """

    def __init__(self, message: str = "retry locally", delay_s: float | None = None) -> None:
        super().__init__(message)
        self.delay_s = delay_s


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
