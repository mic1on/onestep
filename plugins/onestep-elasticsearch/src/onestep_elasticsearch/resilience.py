from __future__ import annotations

import httpx

from onestep import ConnectorErrorKind


def classify_elasticsearch_status(status: int) -> ConnectorErrorKind:
    if status == 429:
        return ConnectorErrorKind.THROTTLED
    if status in {502, 503, 504}:
        return ConnectorErrorKind.TRANSIENT
    if status in {401, 403, 404}:
        return ConnectorErrorKind.MISCONFIGURED
    return ConnectorErrorKind.PERMANENT


def classify_elasticsearch_exception(exc: BaseException) -> ConnectorErrorKind | None:
    if isinstance(exc, httpx.ConnectError):
        return ConnectorErrorKind.DISCONNECTED
    if isinstance(exc, (httpx.ReadTimeout, httpx.WriteError, httpx.ReadError)):
        return ConnectorErrorKind.UNCERTAIN
    if isinstance(exc, httpx.TimeoutException):
        return ConnectorErrorKind.UNCERTAIN
    if isinstance(exc, (ConnectionError, OSError)):
        return ConnectorErrorKind.DISCONNECTED
    return None
