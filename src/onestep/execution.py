from __future__ import annotations

import copy
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Protocol, runtime_checkable
from uuid import UUID


class ExecutionStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    RETRYING = "retrying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


TERMINAL_EXECUTION_STATUSES = frozenset(
    {
        ExecutionStatus.SUCCEEDED,
        ExecutionStatus.FAILED,
        ExecutionStatus.CANCELLED,
        ExecutionStatus.EXPIRED,
    }
)

COMPLETION_STATUSES = frozenset(
    {
        ExecutionStatus.SUCCEEDED,
        ExecutionStatus.RETRYING,
        ExecutionStatus.FAILED,
        ExecutionStatus.CANCELLED,
    }
)


def _text(value: str, field_name: str, *, maximum: int) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    value = value.strip()
    if not value:
        raise ValueError(f"{field_name} must not be empty")
    if len(value) > maximum:
        raise ValueError(f"{field_name} must be at most {maximum} characters")
    return value


def _optional_text(value: str | None, field_name: str, *, maximum: int) -> str | None:
    if value is None:
        return None
    return _text(value, field_name, maximum=maximum)


def _validate_delay(delay_s: float | None) -> float | None:
    if delay_s is None:
        return None
    if isinstance(delay_s, bool) or not isinstance(delay_s, (int, float)):
        raise TypeError("delay_s must be a finite number or None")
    if not math.isfinite(delay_s) or delay_s < 0:
        raise ValueError("delay_s must be finite and >= 0")
    return float(delay_s)


def _validate_expiry(expires_at: datetime | None) -> datetime | None:
    if expires_at is None:
        return None
    if not isinstance(expires_at, datetime):
        raise TypeError("expires_at must be a datetime or None")
    if expires_at.tzinfo is None or expires_at.utcoffset() is None:
        raise ValueError("expires_at must be timezone-aware")
    return expires_at


def _parse_execution_id(value: UUID | str) -> UUID:
    return UUID(str(value))


@dataclass(frozen=True)
class ExecutionErrorDetail:
    kind: str
    exception_type: str
    stage: str | None = None
    backend: str | None = None
    operation: str | None = None
    connector_kind: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", _text(self.kind, "kind", maximum=64))
        object.__setattr__(
            self,
            "exception_type",
            _text(self.exception_type, "exception_type", maximum=255),
        )
        for field_name in ("stage", "backend", "operation", "connector_kind"):
            object.__setattr__(
                self,
                field_name,
                _optional_text(
                    getattr(self, field_name),
                    field_name,
                    maximum=255,
                ),
            )


@dataclass(frozen=True)
class Execution:
    id: UUID
    namespace: str
    task_name: str
    status: ExecutionStatus
    payload: object
    metadata: Mapping[str, object]
    result: object | None
    error: ExecutionErrorDetail | None
    attempts: int
    created_at: datetime
    available_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    cancel_requested_at: datetime | None
    expires_at: datetime | None
    version: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", copy.deepcopy(self.payload))
        object.__setattr__(self, "metadata", copy.deepcopy(self.metadata))
        object.__setattr__(self, "result", copy.deepcopy(self.result))

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_EXECUTION_STATUSES


@dataclass(frozen=True)
class ExecutionPage:
    items: Sequence[Execution]
    next_cursor: str | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(copy.deepcopy(self.items)))


@dataclass(frozen=True)
class ExecutionRequest:
    namespace: str
    task_name: str
    payload: object
    idempotency_key: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)
    delay_s: float | None = None
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "namespace",
            _text(self.namespace, "namespace", maximum=255),
        )
        object.__setattr__(
            self,
            "task_name",
            _text(self.task_name, "task_name", maximum=255),
        )
        object.__setattr__(self, "idempotency_key", _optional_text(
            self.idempotency_key,
            "idempotency_key",
            maximum=255,
        ))
        if not isinstance(self.metadata, Mapping):
            raise TypeError("metadata must be a mapping")
        object.__setattr__(self, "payload", copy.deepcopy(self.payload))
        object.__setattr__(self, "metadata", copy.deepcopy(self.metadata))
        object.__setattr__(self, "delay_s", _validate_delay(self.delay_s))
        object.__setattr__(self, "expires_at", _validate_expiry(self.expires_at))


@dataclass(frozen=True)
class ExecutionQuery:
    namespace: str
    task_name: str | None = None
    status: ExecutionStatus | None = None
    limit: int = 50
    cursor: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "namespace",
            _text(self.namespace, "namespace", maximum=255),
        )
        object.__setattr__(
            self,
            "task_name",
            None
            if self.task_name is None
            else _text(self.task_name, "task_name", maximum=255),
        )
        if self.status is not None and not isinstance(self.status, ExecutionStatus):
            try:
                object.__setattr__(self, "status", ExecutionStatus(self.status))
            except (TypeError, ValueError) as exc:
                raise ValueError("status must be an ExecutionStatus") from exc
        if isinstance(self.limit, bool) or not isinstance(self.limit, int):
            raise TypeError("limit must be an integer")
        if not 1 <= self.limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        if self.cursor is not None:
            if not isinstance(self.cursor, str):
                raise TypeError("cursor must be a string or None")
            if not self.cursor:
                raise ValueError("cursor must not be empty")


@dataclass(frozen=True)
class ExecutionCompletion:
    status: ExecutionStatus
    result: object | None = None
    error: ExecutionErrorDetail | None = None
    delay_s: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, ExecutionStatus):
            try:
                object.__setattr__(self, "status", ExecutionStatus(self.status))
            except (TypeError, ValueError) as exc:
                raise ValueError("status must be an ExecutionStatus") from exc
        if self.status not in COMPLETION_STATUSES:
            raise ValueError(
                "completion status must be succeeded, retrying, failed or cancelled"
            )
        if self.error is not None and not isinstance(self.error, ExecutionErrorDetail):
            raise TypeError("error must be an ExecutionErrorDetail or None")
        object.__setattr__(self, "result", copy.deepcopy(self.result))
        object.__setattr__(self, "delay_s", _validate_delay(self.delay_s))


@dataclass(frozen=True)
class ExecutionLease:
    execution: Execution
    attempt_id: UUID
    lease_token: UUID
    lease_expires_at: datetime


@dataclass(frozen=True)
class HeartbeatResult:
    lease_expires_at: datetime
    cancel_requested: bool


class ExecutionBackend(Protocol):
    async def open(self) -> None: ...

    async def close(self) -> None: ...

    async def submit(self, request: ExecutionRequest) -> Execution: ...

    async def get(self, namespace: str, execution_id: UUID) -> Execution | None: ...

    async def list(self, query: ExecutionQuery) -> ExecutionPage: ...

    async def request_cancel(
        self,
        namespace: str,
        execution_id: UUID,
        *,
        reason: str | None,
    ) -> Execution | None: ...


@runtime_checkable
class LeasedExecutionBackend(ExecutionBackend, Protocol):
    async def claim(
        self,
        namespace: str,
        task_names: Sequence[str],
        limit: int,
        lease_duration_s: float,
        worker_id: str,
    ) -> Sequence[ExecutionLease]: ...

    async def heartbeat(
        self,
        execution_id: UUID,
        attempt_id: UUID,
        lease_token: UUID,
        lease_duration_s: float,
    ) -> HeartbeatResult: ...

    async def complete(
        self,
        execution_id: UUID,
        attempt_id: UUID,
        lease_token: UUID,
        completion: ExecutionCompletion,
    ) -> Execution: ...

    async def release(
        self,
        execution_id: UUID,
        attempt_id: UUID,
        lease_token: UUID,
    ) -> Execution: ...

    async def lease_remaining(self, lease_expires_at: datetime) -> float: ...


@runtime_checkable
class ManagedExecutionDelivery(Protocol):
    execution_id: UUID
    attempt_id: UUID
    cancel_requested: bool

    async def complete_execution(self, completion: ExecutionCompletion) -> None: ...


class ExecutionException(Exception):
    def __init__(self, message: str, execution: Execution | None = None) -> None:
        super().__init__(message)
        self.execution = execution


# Keep the old catch name valid now that the persisted error payload has its
# own unambiguous name.
ExecutionError = ExecutionException


class ExecutionNotFound(ExecutionException):
    def __init__(self, execution_id: UUID | str) -> None:
        self.execution_id = _parse_execution_id(execution_id)
        super().__init__(f"execution {self.execution_id} was not found")


class ExecutionNotReady(ExecutionException):
    def __init__(self, execution: Execution) -> None:
        super().__init__(f"execution {execution.id} is {execution.status.value}", execution)


class ExecutionFailed(ExecutionException):
    def __init__(self, execution: Execution) -> None:
        self.error = execution.error
        super().__init__(f"execution {execution.id} failed", execution)


class ExecutionCancelled(ExecutionException):
    def __init__(self, execution: Execution) -> None:
        super().__init__(f"execution {execution.id} was cancelled", execution)


class ExecutionExpired(ExecutionException):
    def __init__(self, execution: Execution) -> None:
        super().__init__(f"execution {execution.id} expired", execution)


class ExecutionConflict(ExecutionException):
    pass


class ExecutionEncodingError(ExecutionException):
    pass


class ExecutionLeaseLost(ExecutionException, RuntimeError):
    pass


class ExecutionClient:
    def __init__(self, backend: ExecutionBackend, *, namespace: str) -> None:
        self.backend = backend
        self.namespace = _text(namespace, "namespace", maximum=255)

    async def __aenter__(self) -> "ExecutionClient":
        await self.backend.open()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> None:
        await self.backend.close()

    async def submit(
        self,
        task_name: str,
        payload: object,
        *,
        idempotency_key: str | None = None,
        metadata: Mapping[str, object] | None = None,
        delay_s: float | None = None,
        expires_at: datetime | None = None,
    ) -> Execution:
        return await self.backend.submit(
            ExecutionRequest(
                namespace=self.namespace,
                task_name=task_name,
                payload=payload,
                idempotency_key=idempotency_key,
                metadata={} if metadata is None else metadata,
                delay_s=delay_s,
                expires_at=expires_at,
            )
        )

    async def get(self, execution_id: UUID | str) -> Execution | None:
        return await self.backend.get(self.namespace, _parse_execution_id(execution_id))

    async def list(
        self,
        *,
        task_name: str | None = None,
        status: ExecutionStatus | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> ExecutionPage:
        return await self.backend.list(
            ExecutionQuery(
                namespace=self.namespace,
                task_name=task_name,
                status=status,
                limit=limit,
                cursor=cursor,
            )
        )

    async def cancel(
        self,
        execution_id: UUID | str,
        *,
        reason: str | None = None,
    ) -> Execution | None:
        if reason is not None and not isinstance(reason, str):
            raise TypeError("reason must be a string or None")
        normalized_reason = None if reason is None or not reason.strip() else reason.strip()
        if normalized_reason is not None and len(normalized_reason) > 500:
            raise ValueError("reason must be at most 500 characters")
        return await self.backend.request_cancel(
            self.namespace,
            _parse_execution_id(execution_id),
            reason=normalized_reason,
        )

    async def result(self, execution_id: UUID | str) -> object:
        execution = await self.get(execution_id)
        if execution is None:
            raise ExecutionNotFound(execution_id)
        if execution.status is ExecutionStatus.SUCCEEDED:
            return copy.deepcopy(execution.result)
        if execution.status is ExecutionStatus.FAILED:
            raise ExecutionFailed(execution)
        if execution.status is ExecutionStatus.CANCELLED:
            raise ExecutionCancelled(execution)
        if execution.status is ExecutionStatus.EXPIRED:
            raise ExecutionExpired(execution)
        raise ExecutionNotReady(execution)


__all__ = [
    "COMPLETION_STATUSES",
    "TERMINAL_EXECUTION_STATUSES",
    "Execution",
    "ExecutionBackend",
    "ExecutionCancelled",
    "ExecutionClient",
    "ExecutionCompletion",
    "ExecutionConflict",
    "ExecutionEncodingError",
    "ExecutionError",
    "ExecutionErrorDetail",
    "ExecutionException",
    "ExecutionExpired",
    "ExecutionFailed",
    "ExecutionLeaseLost",
    "ExecutionLease",
    "HeartbeatResult",
    "LeasedExecutionBackend",
    "ExecutionNotFound",
    "ExecutionNotReady",
    "ExecutionPage",
    "ExecutionQuery",
    "ExecutionRequest",
    "ExecutionStatus",
    "ManagedExecutionDelivery",
]
