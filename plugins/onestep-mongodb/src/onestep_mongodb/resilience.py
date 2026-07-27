from __future__ import annotations

import re
from dataclasses import dataclass

from bson.errors import InvalidDocument, InvalidStringData
from pymongo.errors import AutoReconnect, BulkWriteError, ConfigurationError, DuplicateKeyError, ExecutionTimeout, InvalidURI, NetworkTimeout, OperationFailure, ServerSelectionTimeoutError

from onestep import ConnectorErrorKind


@dataclass(frozen=True)
class MongoDBErrorCause(Exception):
    code: int | None
    failed_indexes: tuple[int, ...]
    codes: tuple[int | None, ...]
    committed_count: int
    message: str

    def __str__(self) -> str:
        return f"MongoDB error code={self.code} failed_indexes={self.failed_indexes} codes={self.codes}: {self.message}"


_MONGODB_URI_CREDENTIALS = re.compile(r"(?i)\b(mongodb(?:\+srv)?://)[^/@\s]+@")
_SENSITIVE_QUERY_VALUE = re.compile(r"(?i)([?&](?:password|passwd|pwd|secret|token)\s*=)[^&\s]+")


def _redact_message(message: str) -> str:
    message = _MONGODB_URI_CREDENTIALS.sub(r"\1<redacted>@", message)
    return _SENSITIVE_QUERY_VALUE.sub(r"\1<redacted>", message)


def redacted_mongodb_cause(exc: BaseException) -> MongoDBErrorCause:
    details = exc.details if isinstance(exc, BulkWriteError) else {}
    write_errors = details.get("writeErrors", []) if isinstance(details, dict) else []
    failed_indexes = tuple(int(item["index"]) for item in write_errors if isinstance(item, dict) and "index" in item)
    codes = tuple(item.get("code") for item in write_errors if isinstance(item, dict) and "index" in item)
    committed_count = sum(int(details.get(key, 0) or 0) for key in ("nInserted", "nUpserted", "nMatched")) if isinstance(details, dict) else 0
    code = getattr(exc, "code", None)
    if code is None and write_errors:
        code = write_errors[0].get("code")
    if isinstance(exc, BulkWriteError):
        message = "; ".join(
            _redact_message(str(item.get("errmsg", "write error")))[:160]
            for item in write_errors
            if isinstance(item, dict)
        )[:500]
    elif isinstance(exc, (InvalidDocument, InvalidStringData)):
        message = "invalid MongoDB document"
    elif isinstance(exc, (ConfigurationError, InvalidURI)):
        message = "invalid MongoDB configuration"
    else:
        message = _redact_message(str(exc))[:500]
    return MongoDBErrorCause(code, failed_indexes, codes, committed_count, message)


def classify_mongodb_error(exc: BaseException, *, operation: str) -> ConnectorErrorKind | None:
    if isinstance(exc, BulkWriteError):
        details = exc.details if isinstance(exc.details, dict) else {}
        codes = {
            item.get("code")
            for key in ("writeErrors", "writeConcernErrors")
            for item in details.get(key, [])
            if isinstance(item, dict)
        }
        if codes & {13, 18}:
            return ConnectorErrorKind.MISCONFIGURED
        if codes & {50, 16500}:
            return ConnectorErrorKind.THROTTLED
        return ConnectorErrorKind.PERMANENT
    if isinstance(exc, (ConfigurationError, InvalidURI)):
        return ConnectorErrorKind.MISCONFIGURED
    if isinstance(exc, (InvalidDocument, InvalidStringData)):
        return ConnectorErrorKind.PERMANENT
    if isinstance(exc, DuplicateKeyError):
        return ConnectorErrorKind.PERMANENT
    if isinstance(exc, ServerSelectionTimeoutError):
        return ConnectorErrorKind.DISCONNECTED
    if isinstance(exc, NetworkTimeout):
        return ConnectorErrorKind.UNCERTAIN if operation == "send" else ConnectorErrorKind.DISCONNECTED
    if isinstance(exc, ExecutionTimeout):
        return ConnectorErrorKind.UNCERTAIN if operation == "send" else ConnectorErrorKind.TRANSIENT
    if isinstance(exc, OperationFailure):
        if exc.code in {13, 18}:
            return ConnectorErrorKind.MISCONFIGURED
        if exc.code in {286, 280}:
            return ConnectorErrorKind.PERMANENT
        if exc.code in {50, 16500}:
            return ConnectorErrorKind.THROTTLED
        if exc.has_error_label("ResumableChangeStreamError"):
            return ConnectorErrorKind.TRANSIENT
        return ConnectorErrorKind.PERMANENT
    if isinstance(exc, AutoReconnect):
        return ConnectorErrorKind.UNCERTAIN if operation == "send" else ConnectorErrorKind.DISCONNECTED
    return None
