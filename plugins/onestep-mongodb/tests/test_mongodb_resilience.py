from __future__ import annotations

from pymongo.errors import AutoReconnect, BulkWriteError, DuplicateKeyError, OperationFailure

from onestep import ConnectorErrorKind
from onestep_mongodb.resilience import classify_mongodb_error


def test_driver_error_classes() -> None:
    assert classify_mongodb_error(AutoReconnect("lost after submit"), operation="send") is ConnectorErrorKind.UNCERTAIN
    assert classify_mongodb_error(AutoReconnect("selection"), operation="fetch") is ConnectorErrorKind.DISCONNECTED
    assert classify_mongodb_error(DuplicateKeyError("duplicate"), operation="send") is ConnectorErrorKind.PERMANENT
    assert classify_mongodb_error(OperationFailure("auth", code=18), operation="open") is ConnectorErrorKind.MISCONFIGURED
    assert classify_mongodb_error(OperationFailure("history", code=286), operation="fetch") is ConnectorErrorKind.PERMANENT
    assert classify_mongodb_error(BulkWriteError({"writeErrors": [{"index": 0, "code": 16500, "errmsg": "busy"}]}), operation="send") is ConnectorErrorKind.THROTTLED
