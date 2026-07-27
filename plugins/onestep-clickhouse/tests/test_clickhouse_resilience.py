from __future__ import annotations

from onestep import ConnectorErrorKind
from onestep_clickhouse.resilience import (
    classify_clickhouse_error,
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
