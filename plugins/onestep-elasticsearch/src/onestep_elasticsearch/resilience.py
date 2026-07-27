from __future__ import annotations

from onestep import ConnectorErrorKind


def classify_elasticsearch_error(exc: BaseException) -> ConnectorErrorKind | None:
    if isinstance(exc, (ConnectionError, OSError)):
        return ConnectorErrorKind.DISCONNECTED
    return None
