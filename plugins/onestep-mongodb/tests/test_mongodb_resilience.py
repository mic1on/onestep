from __future__ import annotations

import pytest
from bson.errors import InvalidDocument
from onestep_mongodb.resilience import (
    classify_mongodb_error,
    collect_sensitive_tokens,
    redacted_mongodb_cause,
)
from pymongo.errors import (
    AutoReconnect,
    BulkWriteError,
    DuplicateKeyError,
    ExecutionTimeout,
    NetworkTimeout,
    OperationFailure,
    ServerSelectionTimeoutError,
)

from onestep import ConnectorErrorKind


def test_driver_error_classes() -> None:
    assert classify_mongodb_error(AutoReconnect("lost after submit"), operation="send") is ConnectorErrorKind.UNCERTAIN
    assert classify_mongodb_error(AutoReconnect("selection"), operation="fetch") is ConnectorErrorKind.DISCONNECTED
    assert classify_mongodb_error(DuplicateKeyError("duplicate"), operation="send") is ConnectorErrorKind.PERMANENT
    assert classify_mongodb_error(OperationFailure("auth", code=18), operation="open") is ConnectorErrorKind.MISCONFIGURED
    assert classify_mongodb_error(OperationFailure("history", code=286), operation="fetch") is ConnectorErrorKind.PERMANENT
    assert classify_mongodb_error(BulkWriteError({"writeErrors": [{"index": 0, "code": 16500, "errmsg": "busy"}]}), operation="send") is ConnectorErrorKind.THROTTLED


def test_specific_timeout_classes_are_not_shadowed_by_parent_classes() -> None:
    assert classify_mongodb_error(ServerSelectionTimeoutError("selection"), operation="send") is ConnectorErrorKind.DISCONNECTED
    assert classify_mongodb_error(NetworkTimeout("fetch timed out"), operation="fetch") is ConnectorErrorKind.DISCONNECTED
    assert classify_mongodb_error(ExecutionTimeout("query timed out"), operation="fetch") is ConnectorErrorKind.TRANSIENT


def test_redacted_cause_does_not_retain_credentials_or_invalid_documents() -> None:
    connection = redacted_mongodb_cause(AutoReconnect("mongodb://writer:top-secret@mongo/app disconnected"))
    assert "top-secret" not in str(connection)
    assert "writer" not in str(connection)

    invalid = InvalidDocument("Invalid document {'password': 'document-secret'}")
    payload = redacted_mongodb_cause(invalid)
    assert classify_mongodb_error(invalid, operation="send") is ConnectorErrorKind.PERMANENT
    assert "document-secret" not in str(payload)


@pytest.mark.parametrize(
    "client_options",
    [
        {"tlsCertificateKeyFilePassword": "mongo-super-secret"},
        {"proxyPassword": "mongo-super-secret"},
        {"authMechanismProperties": {"AWS_SESSION_TOKEN": "mongo-super-secret"}},
        {"authMechanismProperties": "AWS_SESSION_TOKEN:mongo-super-secret"},
        {"nested": {"password": "mongo-super-secret"}},
    ],
)
def test_redacted_cause_scrubs_pymongo_client_option_secrets(client_options) -> None:
    secret = "mongo-super-secret"
    tokens = collect_sensitive_tokens("mongodb://local", client_options)
    cause = redacted_mongodb_cause(AutoReconnect(f"disconnected with {secret}"), secrets=tokens)
    assert secret not in str(cause)


@pytest.mark.parametrize(
    "query",
    [
        "tlsCertificateKeyFilePassword=mongo-super-secret",
        "proxyPassword=mongo-super-secret",
        "authMechanismProperties=AWS_SESSION_TOKEN:mongo-super-secret",
    ],
)
def test_redacted_cause_scrubs_pymongo_uri_query_secrets(query: str) -> None:
    uri = f"mongodb://host/db?{query}"
    cause = redacted_mongodb_cause(AutoReconnect(uri), secrets=collect_sensitive_tokens(uri))
    assert "mongo-super-secret" not in str(cause)


def test_redacted_cause_scrubs_decoded_uri_password() -> None:
    uri = "mongodb://user:p%40ss@host/db"
    cause = redacted_mongodb_cause(AutoReconnect("authentication failed for p@ss"), secrets=collect_sensitive_tokens(uri))
    assert "p@ss" not in str(cause)
