from __future__ import annotations

import ssl
from collections.abc import Mapping
from dataclasses import dataclass

import httpx

from onestep import ConnectorErrorKind

_REDACTED = "<redacted>"
_MAX_MESSAGE_LENGTH = 500
_SECRET_OPTION_KEYS = frozenset(
    {
        "password",
        "secret",
        "token",
        "access_token",
        "api_key",
        "apikey",
        "credentials",
        "authorization",
        "bearer_token",
        "client_key",
    }
)


@dataclass(frozen=True)
class EsErrorCause(Exception):
    message: str

    def __str__(self) -> str:
        return f"elasticsearch error: {self.message}"


def collect_sensitive_tokens(*config_values: object) -> list[str]:
    """Collect secret substrings that may surface in ES error messages.

    Tokens are derived from connector config: host strings (which may contain
    basic-auth userinfo), known secret fields (password, api_key, bearer_token,
    client_key, etc.), and header values.
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

    return tokens


def _redact_message(message: str, tokens: list[str]) -> str:
    redacted = message
    for token in sorted(tokens, key=len, reverse=True):
        if token:
            redacted = redacted.replace(token, _REDACTED)
    return redacted[:_MAX_MESSAGE_LENGTH]


def redacted_es_cause(
    exc: BaseException, *, secrets: list[str] | None = None
) -> EsErrorCause:
    return EsErrorCause(_redact_message(str(exc), secrets or []))


def classify_elasticsearch_status(status: int) -> ConnectorErrorKind:
    if status == 429:
        return ConnectorErrorKind.THROTTLED
    if status in {502, 503, 504}:
        return ConnectorErrorKind.TRANSIENT
    if status in {401, 403, 404}:
        return ConnectorErrorKind.MISCONFIGURED
    return ConnectorErrorKind.PERMANENT


def classify_elasticsearch_exception(exc: BaseException) -> ConnectorErrorKind | None:
    current: BaseException | None = exc
    while current is not None:
        if isinstance(current, ssl.SSLError):
            return ConnectorErrorKind.MISCONFIGURED
        current = current.__cause__ or current.__context__
    if isinstance(exc, httpx.ConnectTimeout):
        return ConnectorErrorKind.DISCONNECTED
    if isinstance(exc, httpx.ConnectError):
        return ConnectorErrorKind.DISCONNECTED
    if isinstance(exc, (httpx.ReadTimeout, httpx.WriteError, httpx.ReadError)):
        return ConnectorErrorKind.UNCERTAIN
    if isinstance(exc, httpx.TimeoutException):
        return ConnectorErrorKind.UNCERTAIN
    if isinstance(exc, (ConnectionError, OSError)):
        return ConnectorErrorKind.DISCONNECTED
    return None
