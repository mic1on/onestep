from __future__ import annotations

from onestep import ConnectorErrorKind
from onestep_clickhouse.resilience import (
    classify_clickhouse_error,
    collect_sensitive_tokens,
    redacted_clickhouse_cause,
)


class ServerError(Exception):
    def __init__(self, code: int, message: str) -> None:
        self.code = code
        super().__init__(message)


def test_clickhouse_error_code_classes() -> None:
    assert (
        classify_clickhouse_error(ServerError(516, "authentication failed"))
        is ConnectorErrorKind.MISCONFIGURED
    )
    assert (
        classify_clickhouse_error(ServerError(60, "unknown table"))
        is ConnectorErrorKind.MISCONFIGURED
    )
    assert (
        classify_clickhouse_error(ServerError(241, "memory limit"))
        is ConnectorErrorKind.THROTTLED
    )
    assert (
        classify_clickhouse_error(ServerError(53, "type mismatch"))
        is ConnectorErrorKind.PERMANENT
    )
    assert (
        classify_clickhouse_error(TimeoutError("receive timeout"))
        is ConnectorErrorKind.UNCERTAIN
    )


def test_redacted_cause_preserves_code_but_caps_message() -> None:
    cause = redacted_clickhouse_cause(ServerError(53, "x" * 1000))
    assert cause.code == 53
    assert len(str(cause)) < 600


def test_collect_sensitive_tokens_extracts_dsn_userinfo_and_options() -> None:
    tokens = collect_sensitive_tokens(
        "clickhouse://writer:supersecret@ch.internal:9000/default",
        {"password": "supersecret", "access_token": "tok123"},
    )
    # The raw DSN is NOT included (only parsed userinfo + option values)
    assert "clickhouse://writer:supersecret@ch.internal:9000/default" not in tokens
    assert "writer:supersecret" in tokens
    assert "writer:supersecret@" in tokens
    assert "supersecret" in tokens
    assert "tok123" in tokens


def test_collect_sensitive_tokens_handles_dsn_without_userinfo() -> None:
    tokens = collect_sensitive_tokens(
        "clickhouse://ch.internal:9000/default", {}
    )
    assert tokens == []


def test_redacted_cause_scrubs_dsn_credentials_and_option_secrets() -> None:
    exc = ServerError(
        516,
        "Failed to connect to clickhouse://writer:supersecret@ch.internal:9000/default: "
        "Authentication failed for user writer with password 'supersecret'",
    )
    cause = redacted_clickhouse_cause(
        exc,
        dsn="clickhouse://writer:supersecret@ch.internal:9000/default",
        client_options={"password": "supersecret"},
    )
    result = str(cause)
    assert "supersecret" not in result
    assert "writer:supersecret" not in result
    assert "<redacted>" in result
    # Non-sensitive parts should be preserved
    assert "default" in result
    assert "Authentication failed" in result


def test_redacted_cause_without_secrets_keeps_message_intact() -> None:
    exc = ServerError(53, "type mismatch")
    cause = redacted_clickhouse_cause(exc)
    assert "type mismatch" in str(cause)
    assert cause.code == 53
