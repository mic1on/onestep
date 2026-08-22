from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from onestep.resilience import (
    ConnectorErrorKind,
    ConnectorOperation,
    ConnectorOperationError,
)

try:  # pragma: no cover - optional dependency
    from cloudflare import (
        APIConnectionError,
        APIStatusError,
        APITimeoutError,
    )
except ImportError:  # pragma: no cover - optional dependency
    APIConnectionError = None
    APIStatusError = None
    APITimeoutError = None

_REDACTED = "<redacted>"
_MAX_MESSAGE_LENGTH = 500
_SECRET_OPTION_KEYS = frozenset(
    {
        "password",
        "secret",
        "token",
        "api_token",
        "access_token",
        "api_key",
        "apikey",
        "credentials",
        "authorization",
        "bearer_token",
    }
)


@dataclass(frozen=True)
class CFQueuesErrorCause(Exception):
    message: str

    def __str__(self) -> str:
        return f"cloudflare queues error: {self.message}"


def collect_sensitive_tokens(*config_values: object) -> list[str]:
    """Collect secret substrings that may surface in Cloudflare error messages."""
    tokens: list[str] = []
    seen: set[str] = set()

    def add(value: object) -> None:
        if value is None:
            return
        text = str(value)
        if text and text not in seen:
            seen.add(text)
            tokens.append(text)

    for value in config_values:
        if isinstance(value, Mapping):
            for key, item in value.items():
                if str(key).lower() in _SECRET_OPTION_KEYS:
                    add(item)
            continue
        add(value)
    return tokens


def _redact_message(message: str, tokens: list[str]) -> str:
    redacted = message
    for token in sorted(tokens, key=len, reverse=True):
        if token:
            redacted = redacted.replace(token, _REDACTED)
    return redacted[:_MAX_MESSAGE_LENGTH]


def redacted_cf_cause(
    exc: BaseException, *, secrets: list[str] | None = None
) -> CFQueuesErrorCause:
    return CFQueuesErrorCause(_redact_message(str(exc), secrets or []))


def classify_cf_status(status: int) -> ConnectorErrorKind:
    """Map a Cloudflare API HTTP status to a connector error kind."""
    if status == 429:
        return ConnectorErrorKind.THROTTLED
    if status in {500, 502, 503, 504}:
        return ConnectorErrorKind.TRANSIENT
    if status in {401, 403, 404}:
        return ConnectorErrorKind.MISCONFIGURED
    return ConnectorErrorKind.PERMANENT


def classify_cf_error(exc: BaseException) -> ConnectorErrorKind | None:
    """Classify an exception raised by the official ``cloudflare`` SDK.

    ``APIStatusError`` carries a response ``status_code`` and is mapped through
    :func:`classify_cf_status`. ``APITimeoutError`` / ``APIConnectionError`` are
    transport-level failures. Plain socket/OS errors are also treated as
    disconnects so the runtime can degrade and retry the source loop.
    """
    if APITimeoutError is not None and isinstance(exc, APITimeoutError):
        return ConnectorErrorKind.UNCERTAIN
    if APIStatusError is not None and isinstance(exc, APIStatusError):
        return classify_cf_status(exc.status_code)
    if APIConnectionError is not None and isinstance(exc, APIConnectionError):
        return ConnectorErrorKind.DISCONNECTED
    if isinstance(exc, (ConnectionError, OSError, TimeoutError)):
        return ConnectorErrorKind.DISCONNECTED
    return None


def as_cf_connector_operation_error(
    *,
    operation: ConnectorOperation,
    exc: BaseException,
    source_name: str | None = None,
    retry_delay_s: float | None = None,
    secrets: list[str] | None = None,
    backend: str = "cf_queues",
) -> ConnectorOperationError | None:
    kind = classify_cf_error(exc)
    if kind is None:
        return None
    return ConnectorOperationError(
        backend=backend,
        operation=operation,
        kind=kind,
        source_name=source_name,
        retry_delay_s=retry_delay_s,
        cause=redacted_cf_cause(exc, secrets=secrets),
    )
