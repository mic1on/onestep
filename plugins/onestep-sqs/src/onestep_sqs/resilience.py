from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from onestep.resilience import ConnectorErrorKind, ConnectorOperation, ConnectorOperationError

try:  # pragma: no cover - optional dependency
    import botocore.exceptions as botocore_exceptions
except ImportError:  # pragma: no cover - optional dependency
    botocore_exceptions = None

_REDACTED = "<redacted>"
_MAX_MESSAGE_LENGTH = 500
_SECRET_OPTION_KEYS = frozenset(
    {
        "password",
        "secret",
        "token",
        "access_token",
        "aws_secret_access_key",
        "aws_access_key_id",
        "aws_session_token",
        "api_key",
        "apikey",
        "credentials",
        "authorization",
    }
)


@dataclass(frozen=True)
class SQSErrorCause(Exception):
    message: str

    def __str__(self) -> str:
        return f"sqs error: {self.message}"


def collect_sensitive_tokens(*config_values: object) -> list[str]:
    """Collect secret option values that may surface in SQS error messages."""
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


def redacted_sqs_cause(
    exc: BaseException, *, secrets: list[str] | None = None
) -> SQSErrorCause:
    return SQSErrorCause(_redact_message(str(exc), secrets or []))


def classify_sqs_error(exc: BaseException) -> ConnectorErrorKind | None:
    if isinstance(exc, (ConnectionError, OSError, TimeoutError)):
        return ConnectorErrorKind.DISCONNECTED
    if botocore_exceptions is None:
        return None

    transient_types = []
    for name in ("EndpointConnectionError", "ConnectionClosedError", "ReadTimeoutError", "ConnectTimeoutError"):
        error_type = getattr(botocore_exceptions, name, None)
        if isinstance(error_type, type):
            transient_types.append(error_type)
    if transient_types and isinstance(exc, tuple(transient_types)):
        return ConnectorErrorKind.DISCONNECTED

    client_error = getattr(botocore_exceptions, "ClientError", None)
    if isinstance(client_error, type) and isinstance(exc, client_error):
        code = str(exc.response.get("Error", {}).get("Code", "")).lower()
        if code in {
            "throttling",
            "throttlingexception",
            "requestthrottled",
            "toomanyrequestsexception",
            "slowdown",
        }:
            return ConnectorErrorKind.THROTTLED
        if code in {"requesttimeout", "internalerror", "serviceunavailable"}:
            return ConnectorErrorKind.TRANSIENT
        if code in {
            "accessdenied",
            "accessdeniedexception",
            "invalidclienttokenid",
            "signaturedoesnotmatch",
            "aws.simplequeueservice.nonexistentqueue",
        }:
            return ConnectorErrorKind.MISCONFIGURED
        return ConnectorErrorKind.PERMANENT
    return None


def as_sqs_connector_operation_error(
    *,
    operation: ConnectorOperation,
    exc: BaseException,
    source_name: str | None = None,
    retry_delay_s: float | None = None,
    secrets: list[str] | None = None,
    backend: str = "sqs",
) -> ConnectorOperationError | None:
    kind = classify_sqs_error(exc)
    if kind is None:
        return None
    return ConnectorOperationError(
        backend=backend,
        operation=operation,
        kind=kind,
        source_name=source_name,
        retry_delay_s=retry_delay_s,
        cause=redacted_sqs_cause(exc, secrets=secrets),
    )
