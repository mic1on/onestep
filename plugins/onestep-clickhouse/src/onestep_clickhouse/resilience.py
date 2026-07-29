from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from onestep import ConnectorErrorKind

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
class ClickHouseErrorCause(Exception):
    code: int | None
    message: str

    def __str__(self) -> str:
        return f"ClickHouse error code={self.code}: {self.message}"


def collect_sensitive_tokens(dsn: str, client_options: dict[str, Any]) -> list[str]:
    """Collect secret substrings from ClickHouse connector config.

    Returns a list of sensitive tokens (parsed userinfo, password,
    and known secret client_options values) that must be scrubbed from error
    messages before they leave the plugin.

    Non-sensitive parts of the DSN (host, port, database) are intentionally
    excluded so that error messages remain useful for debugging.
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

    try:
        parsed = urlsplit(dsn)
        username = parsed.username
        password = parsed.password
        if username and password:
            add(f"{username}:{password}")
            add(f"{username}:{password}@")
        add(password)
    except ValueError:
        pass

    for key, item in client_options.items():
        if str(key).lower() in _SECRET_OPTION_KEYS:
            add(item)

    return tokens


def _redact_message(message: str, tokens: list[str]) -> str:
    redacted = message
    for token in sorted(tokens, key=len, reverse=True):
        if token:
            redacted = redacted.replace(token, _REDACTED)
    return redacted[:_MAX_MESSAGE_LENGTH]


def redacted_clickhouse_cause(
    exc: BaseException,
    dsn: str | None = None,
    client_options: dict[str, Any] | None = None,
) -> ClickHouseErrorCause:
    tokens = collect_sensitive_tokens(dsn or "", client_options or {})
    return ClickHouseErrorCause(getattr(exc, "code", None), _redact_message(str(exc), tokens))


def classify_clickhouse_error(exc: BaseException) -> ConnectorErrorKind | None:
    if isinstance(exc, TimeoutError):
        return ConnectorErrorKind.UNCERTAIN
    if isinstance(exc, (ConnectionError, OSError)):
        return ConnectorErrorKind.DISCONNECTED
    code = getattr(exc, "code", None)
    if code in {202, 203, 209, 210}:
        return ConnectorErrorKind.DISCONNECTED
    if code in {159, 164, 241, 252}:
        return ConnectorErrorKind.THROTTLED
    if code in {60, 62, 81, 516}:
        return ConnectorErrorKind.MISCONFIGURED
    if code in {6, 27, 53, 117, 386}:
        return ConnectorErrorKind.PERMANENT
    if code is not None:
        return ConnectorErrorKind.TRANSIENT
    return None
