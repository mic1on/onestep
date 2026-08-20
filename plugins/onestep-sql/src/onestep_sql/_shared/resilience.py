"""Shared secret-redaction scaffolding for the onestep-sql backends.

Phase 2 of the mysql/postgres consolidation (issue #133, design §3.1) keeps
exactly one copy of the resilience machinery that previously existed in
parallel in both backend ``resilience.py`` modules and differed only in the
backend name (see the retired ``scripts/check_plugin_drift.py``):

* the secret-token collection used to scrub error causes
  (:func:`collect_sensitive_tokens`);
* the message redaction itself (:func:`redact_message`);
* the redacted error-cause base class (:class:`SQLErrorCause`) that backends
  subclass with their own ``backend`` label so ``str(cause)`` keeps its
  per-database prefix;
* the :class:`~onestep.resilience.ConnectorOperationError` factory
  (:func:`as_connector_operation_error`).

The SQLAlchemy error-classification tables stay in each backend's
``resilience.py`` because they encode genuinely different server error
messages (``server has gone away`` vs ``server closed the connection``,
``deadlock found`` vs ``deadlock detected`` and so on).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import ClassVar
from urllib.parse import urlsplit

from onestep.resilience import (
    ConnectorErrorKind,
    ConnectorOperation,
    ConnectorOperationError,
)

REDACTED = "<redacted>"
MAX_MESSAGE_LENGTH = 500
SECRET_OPTION_KEYS = frozenset(
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


def collect_sensitive_tokens(*config_values: object) -> list[str]:
    """Collect secret substrings that may surface in SQL backend error messages.

    SQLAlchemy masks the password in its rendered URL, but the underlying DBAPI
    ``orig`` exception can still echo credentials (e.g. after echoing the full
    connection string). Tokens are derived from connector config (raw DSN +
    parsed userinfo + known secret option values) and used to scrub error
    causes before they leave the plugin.
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

    def collect_mapping(value: Mapping[object, object]) -> None:
        for key, item in value.items():
            if str(key).lower() in SECRET_OPTION_KEYS:
                add(item)
            elif isinstance(item, Mapping):
                collect_mapping(item)

    for value in config_values:
        if isinstance(value, Mapping):
            collect_mapping(value)
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


def redact_message(message: str, tokens: list[str]) -> str:
    redacted = message
    for token in sorted(tokens, key=len, reverse=True):
        if token:
            redacted = redacted.replace(token, REDACTED)
    return redacted[:MAX_MESSAGE_LENGTH]


@dataclass(frozen=True)
class SQLErrorCause(Exception):
    """Redacted cause attached to normalized SQL connector errors.

    Backends subclass this with their own ``backend`` label so the rendered
    message keeps its per-database prefix (``mysql error: ...`` /
    ``postgres error: ...``) while the dataclass shape, freezing and identity
    semantics are implemented once here.
    """

    message: str

    backend: ClassVar[str] = "sql"

    def __str__(self) -> str:
        return f"{self.backend} error: {self.message}"


def as_connector_operation_error(
    *,
    backend: str,
    operation: ConnectorOperation,
    exc: BaseException,
    classify: Callable[[BaseException], ConnectorErrorKind | None],
    redacted_cause: Callable[..., Exception],
    source_name: str | None = None,
    retry_delay_s: float | None = None,
    secrets: list[str] | None = None,
) -> ConnectorOperationError | None:
    """Normalize ``exc`` using the backend's classifier and redacted cause."""
    kind = classify(exc)
    if kind is None:
        return None
    return ConnectorOperationError(
        backend=backend,
        operation=operation,
        kind=kind,
        source_name=source_name,
        retry_delay_s=retry_delay_s,
        cause=redacted_cause(exc, secrets=secrets),
    )
