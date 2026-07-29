from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

from onestep.resilience import ConnectorErrorKind, ConnectorOperation, ConnectorOperationError

try:  # pragma: no cover - optional dependency
    import aiokafka.errors as aiokafka_errors
except ImportError:  # pragma: no cover - optional dependency
    aiokafka_errors = None

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
        "sasl_password",
        "sasl_plain_password",
    }
)


@dataclass(frozen=True)
class KafkaErrorCause(Exception):
    message: str

    def __str__(self) -> str:
        return f"kafka error: {self.message}"


def collect_sensitive_tokens(*config_values: object) -> list[str]:
    """Collect secret substrings that may surface in Kafka error messages.

    Tokens are derived from connector config: raw bootstrap-server strings
    (always included as a catch-all), their parsed userinfo, and known secret
    keys inside option mappings. Error causes are scrubbed against these before
    they leave the plugin.
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


def redacted_kafka_cause(
    exc: BaseException, *, secrets: list[str] | None = None
) -> KafkaErrorCause:
    return KafkaErrorCause(_redact_message(str(exc), secrets or []))


def classify_kafka_error(exc: BaseException) -> ConnectorErrorKind | None:
    if isinstance(exc, (ConnectionError, OSError, TimeoutError)):
        return ConnectorErrorKind.DISCONNECTED
    message = str(exc).lower()
    if "connection" in message and "closed" in message:
        return ConnectorErrorKind.DISCONNECTED
    if "timed out" in message or "timeout" in message:
        return ConnectorErrorKind.TRANSIENT
    if aiokafka_errors is None:
        return None

    kafka_error = getattr(aiokafka_errors, "KafkaError", None)
    if isinstance(kafka_error, type) and isinstance(exc, kafka_error):
        retriable = getattr(exc, "retriable", None)
        if callable(retriable) and retriable():
            return ConnectorErrorKind.TRANSIENT
        invalid_config = getattr(aiokafka_errors, "InvalidConfigurationError", None)
        if isinstance(invalid_config, type) and isinstance(exc, invalid_config):
            return ConnectorErrorKind.MISCONFIGURED
        return ConnectorErrorKind.PERMANENT
    return None


def as_kafka_connector_operation_error(
    *,
    operation: ConnectorOperation,
    exc: BaseException,
    source_name: str | None = None,
    retry_delay_s: float | None = None,
    secrets: list[str] | None = None,
) -> ConnectorOperationError | None:
    kind = classify_kafka_error(exc)
    if kind is None:
        return None
    return ConnectorOperationError(
        backend="kafka",
        operation=operation,
        kind=kind,
        source_name=source_name,
        retry_delay_s=retry_delay_s,
        cause=redacted_kafka_cause(exc, secrets=secrets),
    )
