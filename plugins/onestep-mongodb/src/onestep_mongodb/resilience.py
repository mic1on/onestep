from __future__ import annotations

from onestep import ConnectorErrorKind


def classify_mongodb_error(exc: BaseException, *, operation: str) -> ConnectorErrorKind | None:
    return None
