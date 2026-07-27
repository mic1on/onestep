from __future__ import annotations

from dataclasses import dataclass

from onestep import ConnectorErrorKind


@dataclass(frozen=True)
class ClickHouseErrorCause(Exception):
    code: int | None
    message: str

    def __str__(self) -> str:
        return f"ClickHouse error code={self.code}: {self.message}"


def redacted_clickhouse_cause(exc: BaseException) -> ClickHouseErrorCause:
    return ClickHouseErrorCause(getattr(exc, "code", None), str(exc)[:500])


def classify_clickhouse_error(exc: BaseException) -> ConnectorErrorKind | None:
    if isinstance(exc, TimeoutError):
        return ConnectorErrorKind.UNCERTAIN
    if isinstance(exc, (ConnectionError, OSError)):
        return ConnectorErrorKind.DISCONNECTED
    code = getattr(exc, "code", None)
    if code in {202, 203, 209, 210}:
        return ConnectorErrorKind.DISCONNECTED
    if code in {159, 164, 241, 252}:
        return ConnectorErrorKind.THROTTLED
    if code in {60, 62, 81, 516}:
        return ConnectorErrorKind.MISCONFIGURED
    if code in {6, 27, 53, 117, 386}:
        return ConnectorErrorKind.PERMANENT
    if code is not None:
        return ConnectorErrorKind.TRANSIENT
    return None
