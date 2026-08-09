from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import json
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from uuid import UUID, uuid4

import sqlalchemy as sa

from onestep.capture.codec import CaptureEncodingError, decode_value, encode_value
from onestep.execution import (
    Execution,
    ExecutionBackend,
    ExecutionClient,
    ExecutionCompletion,
    ExecutionConflict,
    ExecutionEncodingError,
    ExecutionError,
    ExecutionPage,
    ExecutionQuery,
    ExecutionRequest,
    ExecutionStatus,
)

from .execution_schema import ExecutionTables, build_execution_tables


class StaleExecutionLease(RuntimeError):
    pass


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


class PostgresExecutionBackend(ExecutionBackend):
    def __init__(
        self,
        *,
        connector: Any,
        table: str = "onestep_executions",
        attempts_table: str = "onestep_execution_attempts",
        auto_create: bool = True,
        max_payload_bytes: int = 1024 * 1024,
        max_metadata_bytes: int = 64 * 1024,
        max_result_bytes: int = 1024 * 1024,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.connector = connector
        self.engine = connector.engine
        self.tables: ExecutionTables = build_execution_tables(
            executions_table=table,
            attempts_table=attempts_table,
        )
        self.table_name = table
        self.attempts_table_name = attempts_table
        self.auto_create = bool(auto_create)
        self.max_payload_bytes = max_payload_bytes
        self.max_metadata_bytes = max_metadata_bytes
        self.max_result_bytes = max_result_bytes
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._ready = False
        self._ready_lock = threading.Lock()
        for name, value in (
            ("max_payload_bytes", max_payload_bytes),
            ("max_metadata_bytes", max_metadata_bytes),
            ("max_result_bytes", max_result_bytes),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")

    async def open(self) -> None:
        await asyncio.to_thread(self._ensure_ready_sync)

    async def submit(self, request: ExecutionRequest) -> Execution:
        encoded = self._encode_submission(request)
        return await asyncio.to_thread(self._submit_sync, request, encoded)

    async def get(self, namespace: str, execution_id: UUID) -> Execution | None:
        return await asyncio.to_thread(self._get_sync, namespace, execution_id)

    async def list(self, query: ExecutionQuery) -> ExecutionPage:
        return await asyncio.to_thread(self._list_sync, query)

    async def request_cancel(
        self,
        namespace: str,
        execution_id: UUID,
        *,
        reason: str | None,
    ) -> Execution | None:
        return await asyncio.to_thread(
            self._request_cancel_sync,
            namespace,
            execution_id,
            reason,
        )

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime")
        return value.astimezone(timezone.utc)

    def _ensure_ready_sync(self) -> None:
        if self._ready:
            return
        with self._ready_lock:
            if self._ready:
                return
            if self.auto_create:
                self.tables.metadata.create_all(
                    self.engine,
                    tables=[self.tables.executions, self.tables.attempts],
                    checkfirst=True,
                )
            else:
                inspector = sa.inspect(self.engine)
                missing = [
                    name
                    for name in (self.table_name, self.attempts_table_name)
                    if not inspector.has_table(name)
                ]
                if missing:
                    raise RuntimeError(
                        "missing execution tables: " + ", ".join(missing)
                    )
            self._ready = True

    def _encode_submission(self, request: ExecutionRequest) -> dict[str, Any]:
        if "onestep.execution" in request.metadata:
            raise ValueError("metadata key 'onestep.execution' is reserved")
        payload = self._encode_value(request.payload, "payload")
        metadata = self._encode_value(dict(request.metadata), "metadata")
        if self._encoded_size(payload) > self.max_payload_bytes:
            raise ExecutionEncodingError("execution payload exceeds the configured limit")
        if self._encoded_size(metadata) > self.max_metadata_bytes:
            raise ExecutionEncodingError("execution metadata exceeds the configured limit")
        digest_payload = {
            "namespace": request.namespace,
            "task_name": request.task_name,
            "payload": payload,
            "metadata": metadata,
            "delay_s": request.delay_s,
            "expires_at": request.expires_at.isoformat() if request.expires_at else None,
        }
        digest = hashlib.sha256(
            json.dumps(
                digest_payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("ascii")
        ).hexdigest()
        return {"payload": payload, "metadata": metadata, "digest": digest}

    def _encode_value(self, value: Any, field: str) -> Any:
        try:
            return encode_value(copy.deepcopy(value))
        except CaptureEncodingError as exc:
            raise ExecutionEncodingError(f"cannot encode execution {field}: {exc}") from exc

    @staticmethod
    def _encoded_size(value: Any) -> int:
        return len(
            json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("ascii")
        )

    def _submit_sync(
        self,
        request: ExecutionRequest,
        encoded: dict[str, Any],
    ) -> Execution:
        self._ensure_ready_sync()
        now = self._now()
        execution_id = uuid4()
        available_at = now + timedelta(seconds=request.delay_s or 0)
        values = {
            "id": execution_id,
            "namespace": request.namespace,
            "task_name": request.task_name,
            "status": ExecutionStatus.QUEUED.value,
            "payload": encoded["payload"],
            "metadata": encoded["metadata"],
            "idempotency_key": request.idempotency_key,
            "submission_digest": encoded["digest"] if request.idempotency_key else None,
            "attempts": 0,
            "available_at": available_at,
            "created_at": now,
            "updated_at": now,
            "version": 0,
            "expires_at": request.expires_at,
        }
        try:
            with self.engine.begin() as conn:
                conn.execute(sa.insert(self.tables.executions).values(**values))
        except sa.exc.IntegrityError:
            if request.idempotency_key is None:
                raise
            with self.engine.begin() as conn:
                row = conn.execute(
                    sa.select(self.tables.executions).where(
                        self.tables.executions.c.namespace == request.namespace,
                        self.tables.executions.c.task_name == request.task_name,
                        self.tables.executions.c.idempotency_key == request.idempotency_key,
                    )
                ).mappings().one_or_none()
            if row is None:
                raise
            if row["submission_digest"] != encoded["digest"]:
                raise ExecutionConflict(
                    "idempotency key was already used with a different submission"
                )
            return self._row_to_execution(row)
        return self._get_sync(request.namespace, execution_id)

    def _get_sync(self, namespace: str, execution_id: UUID) -> Execution | None:
        self._ensure_ready_sync()
        with self.engine.begin() as conn:
            row = conn.execute(
                sa.select(self.tables.executions).where(
                    self.tables.executions.c.namespace == namespace,
                    self.tables.executions.c.id == execution_id,
                )
            ).mappings().one_or_none()
        return None if row is None else self._row_to_execution(row)

    def _list_sync(self, query: ExecutionQuery) -> ExecutionPage:
        self._ensure_ready_sync()
        stmt = sa.select(self.tables.executions).where(
            self.tables.executions.c.namespace == query.namespace
        )
        if query.task_name is not None:
            stmt = stmt.where(self.tables.executions.c.task_name == query.task_name)
        if query.status is not None:
            stmt = stmt.where(self.tables.executions.c.status == query.status.value)
        if query.cursor is not None:
            created_at, execution_id = self._decode_cursor(query.cursor)
            created_column = self.tables.executions.c.created_at
            id_column = self.tables.executions.c.id
            stmt = stmt.where(
                sa.or_(
                    created_column < created_at,
                    sa.and_(created_column == created_at, id_column < execution_id),
                )
            )
        stmt = stmt.order_by(
            self.tables.executions.c.created_at.desc(),
            self.tables.executions.c.id.desc(),
        ).limit(query.limit + 1)
        with self.engine.begin() as conn:
            rows = conn.execute(stmt).mappings().all()
        has_more = len(rows) > query.limit
        selected = rows[: query.limit]
        items = tuple(self._row_to_execution(row) for row in selected)
        next_cursor = None
        if has_more and items:
            last = items[-1]
            next_cursor = self._encode_cursor(last.created_at, last.id)
        return ExecutionPage(items=items, next_cursor=next_cursor)

    def _request_cancel_sync(
        self,
        namespace: str,
        execution_id: UUID,
        reason: str | None,
    ) -> Execution | None:
        self._ensure_ready_sync()
        now = self._now()
        with self.engine.begin() as conn:
            row = conn.execute(
                sa.select(self.tables.executions)
                .where(
                    self.tables.executions.c.namespace == namespace,
                    self.tables.executions.c.id == execution_id,
                )
                .with_for_update()
            ).mappings().one_or_none()
            if row is None:
                return None
            status = row["status"]
            if status in {ExecutionStatus.QUEUED.value, ExecutionStatus.RETRYING.value}:
                values = {
                    "status": ExecutionStatus.CANCELLED.value,
                    "cancel_reason": reason,
                    "cancel_requested_at": now,
                    "finished_at": now,
                    "updated_at": now,
                    "version": row["version"] + 1,
                }
            elif status == ExecutionStatus.RUNNING.value:
                values = {
                    "status": ExecutionStatus.CANCEL_REQUESTED.value,
                    "cancel_reason": reason,
                    "cancel_requested_at": now,
                    "updated_at": now,
                    "version": row["version"] + 1,
                }
            else:
                return self._row_to_execution(row)
            conn.execute(
                sa.update(self.tables.executions)
                .where(self.tables.executions.c.id == execution_id)
                .values(**values)
            )
            refreshed = conn.execute(
                sa.select(self.tables.executions).where(
                    self.tables.executions.c.id == execution_id
                )
            ).mappings().one()
        return self._row_to_execution(refreshed)

    def _row_to_execution(self, row: Mapping[str, Any]) -> Execution:
        try:
            raw_error = decode_value(row["error"]) if row["error"] is not None else None
            error = None if raw_error is None else ExecutionError(**raw_error)
            return Execution(
                id=row["id"],
                namespace=row["namespace"],
                task_name=row["task_name"],
                status=ExecutionStatus(row["status"]),
                payload=decode_value(row["payload"]),
                metadata=decode_value(row["metadata"]),
                result=decode_value(row["result"]) if row["result"] is not None else None,
                error=error,
                attempts=row["attempts"],
                created_at=_aware_utc(row["created_at"]),
                available_at=_aware_utc(row["available_at"]),
                started_at=_optional_aware_utc(row["started_at"]),
                finished_at=_optional_aware_utc(row["finished_at"]),
                cancel_requested_at=_optional_aware_utc(row["cancel_requested_at"]),
                expires_at=_optional_aware_utc(row["expires_at"]),
                version=row["version"],
            )
        except Exception as exc:
            if isinstance(exc, ExecutionEncodingError):
                raise
            raise ExecutionEncodingError(f"cannot decode execution {row.get('id')}: {exc}") from exc

    @staticmethod
    def _encode_cursor(created_at: datetime, execution_id: UUID) -> str:
        payload = json.dumps(
            {"v": 1, "created_at": created_at.isoformat(), "id": str(execution_id)},
            separators=(",", ":"),
        ).encode("utf-8")
        return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            raw = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
            if set(raw) != {"v", "created_at", "id"} or raw["v"] != 1:
                raise ValueError("unknown cursor version")
            created_at = datetime.fromisoformat(raw["created_at"])
            if created_at.tzinfo is None or created_at.utcoffset() is None:
                raise ValueError("cursor created_at must be timezone-aware")
            return created_at.astimezone(timezone.utc), UUID(str(raw["id"]))
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("invalid execution cursor") from exc


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _optional_aware_utc(value: datetime | None) -> datetime | None:
    return None if value is None else _aware_utc(value)


__all__ = [
    "ExecutionLease",
    "HeartbeatResult",
    "PostgresExecutionBackend",
    "StaleExecutionLease",
]
