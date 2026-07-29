from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from onestep.resilience import ConnectorErrorKind, ConnectorOperation, ConnectorOperationError

try:  # pragma: no cover - optional dependency
    import aio_pika.exceptions as aio_pika_exceptions
except ImportError:  # pragma: no cover - optional dependency
    aio_pika_exceptions = None

try:  # pragma: no cover - optional dependency
    import aiormq.exceptions as aiormq_exceptions
except ImportError:  # pragma: no cover - optional dependency
    aiormq_exceptions = None

_REDACTED = "<redacted>"
_MAX_MESSAGE_LENGTH = 500
_SECRET_OPTION_KEYS = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "secret",
        "token",
        "access_token",
        "api_key",
        "apikey",
        "credentials",
        "authorization",
    }
)


@dataclass(frozen=True)
class RabbitMQErrorCause(Exception):
    message: str

    def __str__(self) -> str:
        return f"rabbitmq error: {self.message}"


def collect_sensitive_tokens(*config_values: object) -> list[str]:
    """Collect secret substrings that may surface in RabbitMQ error messages.

    Tokens are derived from connector config: raw AMQP URI/connection-string
    values (always included as a catch-all), their parsed userinfo, and known
    secret keys inside option mappings. Error causes are scrubbed against these
    before they leave the plugin.
    """
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
        try:
            parsed = urlsplit(str(value))
        except ValueError:
            continue
        username = parsed.username
        password = parsed.password
        if username and password:
            add(f"{username}:{password}")
            add(f"{username}:{password}@")
        add(password)
    return tokens


def _redact_message(message: str, tokens: list[str]) -> str:
    redacted = message
    for token in sorted(tokens, key=len, reverse=True):
        if token:
            redacted = redacted.replace(token, _REDACTED)
    return redacted[:_MAX_MESSAGE_LENGTH]


def redacted_rabbitmq_cause(
    exc: BaseException, *, secrets: list[str] | None = None
) -> RabbitMQErrorCause:
    return RabbitMQErrorCause(_redact_message(str(exc), secrets or []))


def classify_rabbitmq_error(exc: BaseException) -> ConnectorErrorKind | None:
    if isinstance(exc, (ConnectionError, OSError)):
        return ConnectorErrorKind.DISCONNECTED
    if isinstance(exc, RuntimeError):
        message = str(exc).lower()
        if "closed" in message or "no active transport" in message:
            return ConnectorErrorKind.DISCONNECTED

    if aio_pika_exceptions is not None:
        result = _classify_amqp_error(exc, aio_pika_exceptions)
        if result is not None:
            return result
    if aiormq_exceptions is not None:
        result = _classify_amqp_error(exc, aiormq_exceptions)
        if result is not None:
            return result
    return None


def _classify_amqp_error(exc: BaseException, module: Any) -> ConnectorErrorKind | None:
    disconnected_names = {
        "AMQPConnectionError",
        "AMQPChannelError",
        "ConnectionClosed",
        "ChannelClosed",
        "ChannelInvalidStateError",
        "DeliveryError",
    }
    misconfigured_names = {
        "AuthenticationError",
        "ProbableAuthenticationError",
        "IncompatibleProtocolError",
    }
    permanent_names = {
        "ChannelNotFoundEntity",
        "ChannelPreconditionFailed",
    }
    for name in disconnected_names:
        error_type = getattr(module, name, None)
        if isinstance(error_type, type) and isinstance(exc, error_type):
            return ConnectorErrorKind.DISCONNECTED
    for name in misconfigured_names:
        error_type = getattr(module, name, None)
        if isinstance(error_type, type) and isinstance(exc, error_type):
            return ConnectorErrorKind.MISCONFIGURED
    for name in permanent_names:
        error_type = getattr(module, name, None)
        if isinstance(error_type, type) and isinstance(exc, error_type):
            return ConnectorErrorKind.PERMANENT
    return None


def as_rabbitmq_connector_operation_error(
    *,
    operation: ConnectorOperation,
    exc: BaseException,
    source_name: str | None = None,
    retry_delay_s: float | None = None,
    secrets: list[str] | None = None,
) -> ConnectorOperationError | None:
    kind = classify_rabbitmq_error(exc)
    if kind is None:
        return None
    return ConnectorOperationError(
        backend="rabbitmq",
        operation=operation,
        kind=kind,
        source_name=source_name,
        retry_delay_s=retry_delay_s,
        cause=redacted_rabbitmq_cause(exc, secrets=secrets),
    )
