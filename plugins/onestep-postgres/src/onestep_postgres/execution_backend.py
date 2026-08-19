from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from uuid import UUID, uuid4

import sqlalchemy as sa

from onestep.capture.codec import CaptureEncodingError, decode_value, encode_value
from onestep.execution import (
    Execution,
    ExecutionCompletion,
    ExecutionConflict,
    ExecutionEncodingError,
    ExecutionErrorDetail,
    ExecutionLease,
    ExecutionLeaseLost,
    ExecutionPage,
    ExecutionQuery,
    ExecutionRequest,
    ExecutionStatus,
    HeartbeatResult,
    LeasedExecutionBackend,
)

from .execution_schema import ExecutionTables, build_execution_tables


class StaleExecutionLease(ExecutionLeaseLost):
    pass


class PostgresExecutionBackend(LeasedExecutionBackend):
    def __init__(
        self,
        *,
        dsn: str | None = None,
        connector: Any | None = None,
        table: str = "onestep_executions",
        attempts_table: str = "onestep_execution_attempts",
        auto_create: bool = True,
        max_payload_bytes: int = 1024 * 1024,
        max_metadata_bytes: int = 64 * 1024,
        max_result_bytes: int = 1024 * 1024,
        reclaim_batch_size: int = 100,
        clock: Callable[[], datetime] | None = None,
        **engine_options: Any,
    ) -> None:
        if (dsn is None) == (connector is None):
            raise ValueError("pass exactly one of dsn or connector")
        if dsn is not None:
            if not isinstance(dsn, str):
                raise TypeError("dsn must be a string")
            if not dsn.strip():
                raise ValueError("dsn must not be empty")
        if connector is not None and engine_options:
            raise ValueError("engine options require a dsn")
        self._dsn = dsn
        self._engine_options = dict(engine_options)
        self._connector = connector
        self._owns_connector = dsn is not None
        self._pid = os.getpid()
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
        self.reclaim_batch_size = reclaim_batch_size
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._ready = False
        self._open_count = 0
        self._ready_lock: asyncio.Lock | None = None
        self._ready_loop: asyncio.AbstractEventLoop | None = None
        for name, value in (
            ("max_payload_bytes", max_payload_bytes),
            ("max_metadata_bytes", max_metadata_bytes),
            ("max_result_bytes", max_result_bytes),
            ("reclaim_batch_size", reclaim_batch_size),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")

    @classmethod
    def from_connector(
        cls,
        connector: Any,
        **kwargs: Any,
    ) -> "PostgresExecutionBackend":
        return cls(connector=connector, **kwargs)

    @property
    def connector(self) -> Any | None:
        return self._connector

    @property
    def engine(self) -> Any:
        return self._ensure_connector().engine

    def source(
        self,
        *,
        namespace: str,
        task_names: Sequence[str],
        batch_size: int = 100,
        poll_interval_s: float = 1.0,
        lease_duration_s: float = 90.0,
        heartbeat_interval_s: float = 30.0,
        worker_id: str = "onestep-worker",
    ) -> Any:
        from .execution_source import PostgresExecutionSource

        return PostgresExecutionSource(
            backend=self,
            namespace=namespace,
            task_names=task_names,
            batch_size=batch_size,
            poll_interval_s=poll_interval_s,
            lease_duration_s=lease_duration_s,
            heartbeat_interval_s=heartbeat_interval_s,
            worker_id=worker_id,
        )

    async def open(self) -> None:
        async with self._runtime_ready_lock():
            await self._ensure_ready_locked()
            self._open_count += 1

    async def close(self) -> None:
        async with self._runtime_ready_lock():
            current_pid = os.getpid()
            if current_pid != self._pid:
                if not self._owns_connector:
                    raise self._fork_error(current_pid)
                self._discard_inherited_connector(current_pid)
                return
            if self._open_count > 0:
                self._open_count -= 1
            if self._open_count > 0:
                return
            self._ready = False
            if self._owns_connector and self._connector is not None:
                await self._connector.engine.dispose()
                self._connector = None

    async def submit(self, request: ExecutionRequest) -> Execution:
        encoded = self._encode_submission(request)
        return await self._submit(request, encoded)

    async def get(self, namespace: str, execution_id: UUID) -> Execution | None:
        return await self._get(namespace, execution_id)

    async def list(self, query: ExecutionQuery) -> ExecutionPage:
        return await self._list(query)

    async def request_cancel(
        self,
        namespace: str,
        execution_id: UUID,
        *,
        reason: str | None,
    ) -> Execution | None:
        return await self._request_cancel(namespace, execution_id, reason)

    async def claim(
        self,
        namespace: str,
        task_names: Sequence[str],
        limit: int,
        lease_duration_s: float,
        worker_id: str,
    ) -> tuple[ExecutionLease, ...]:
        return await self._claim(
            namespace,
            tuple(task_names),
            limit,
            lease_duration_s,
            worker_id,
        )

    async def heartbeat(
        self,
        execution_id: UUID,
        attempt_id: UUID,
        lease_token: UUID,
        lease_duration_s: float,
    ) -> HeartbeatResult:
        return await self._heartbeat(
            execution_id,
            attempt_id,
            lease_token,
            lease_duration_s,
        )

    async def complete(
        self,
        execution_id: UUID,
        attempt_id: UUID,
        lease_token: UUID,
        completion: ExecutionCompletion,
    ) -> Execution:
        return await self._complete(
            execution_id,
            attempt_id,
            lease_token,
            completion,
        )

    async def release(
        self,
        execution_id: UUID,
        attempt_id: UUID,
        lease_token: UUID,
    ) -> Execution:
        return await self._release(
            execution_id,
            attempt_id,
            lease_token,
        )

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime")
        return value.astimezone(timezone.utc)

    async def _transaction_now(self, conn: Any) -> datetime:
        # PostgreSQL's transaction timestamp is stable for all lease checks in
        # this transaction. SQLite uses the injected clock for deterministic
        # unit tests and non-PostgreSQL compatibility.
        if conn.dialect.name == "postgresql":
            value = (
                await conn.execute(sa.select(sa.func.current_timestamp()))
            ).scalar_one()
            return _aware_utc(value)
        return self._now()

    async def _lease_remaining(self, lease_expires_at: datetime) -> float:
        await self._ensure_ready()
        async with self.engine.begin() as conn:
            now = await self._transaction_now(conn)
        return (lease_expires_at - now).total_seconds()

    async def lease_remaining(self, lease_expires_at: datetime) -> float:
        return await self._lease_remaining(lease_expires_at)

    def _runtime_ready_lock(self) -> asyncio.Lock:
        current_loop = asyncio.get_running_loop()
        if self._ready_lock is None or self._ready_loop is not current_loop:
            self._ready_lock = asyncio.Lock()
            self._ready_loop = current_loop
        return self._ready_lock

    def _ensure_connector(self) -> Any:
        # Lock-free by design: constructing the connector performs no I/O
        # (create_async_engine is lazy). Callers that run statements must hold
        # the ready lock and use this helper inside it, or go through
        # _ensure_ready/_ensure_ready_locked.
        current_pid = os.getpid()
        if current_pid != self._pid:
            if not self._owns_connector:
                raise self._fork_error(current_pid)
            self._discard_inherited_connector(current_pid)
        if self._connector is None:
            from .connector import PostgresConnector

            assert self._dsn is not None
            self._connector = PostgresConnector(self._dsn, **self._engine_options)
        return self._connector

    def _discard_inherited_connector(self, current_pid: int) -> None:
        # Drop the inherited engine reference WITHOUT disposing it:
        # AsyncEngine.dispose() is a coroutine and must not be driven in a
        # forked child over the inherited event loop. The child's sockets are
        # fd copies of the parent's; garbage collecting them in the child only
        # closes the child's descriptors and never touches the parent's
        # server connections.
        self._connector = None
        self._ready = False
        self._open_count = 0
        self._pid = current_pid

    def _fork_error(self, current_pid: int) -> RuntimeError:
        return RuntimeError(
            "PostgresExecutionBackend cannot reuse an externally supplied connector "
            f"across a process boundary (created in pid {self._pid}, current pid "
            f"{current_pid}); create the PostgresConnector in the child process"
        )

    async def _ensure_ready(self) -> None:
        async with self._runtime_ready_lock():
            await self._ensure_ready_locked()

    async def _ensure_ready_locked(self) -> None:
        connector = self._ensure_connector()
        if self._ready:
            return
        engine = connector.engine
        if self.auto_create:
            tables = [self.tables.executions, self.tables.attempts]
            if engine.dialect.name == "postgresql":
                lock_name = f"{self.table_name}\0{self.attempts_table_name}"
                lock_key = int.from_bytes(
                    hashlib.sha256(lock_name.encode("utf-8")).digest()[:8],
                    byteorder="big",
                    signed=True,
                )
                async with engine.begin() as conn:
                    await conn.execute(sa.select(sa.func.pg_advisory_xact_lock(lock_key)))
                    await conn.run_sync(
                        lambda sync_conn: self.tables.metadata.create_all(
                            sync_conn,
                            tables=tables,
                            checkfirst=True,
                        )
                    )
            else:
                async with engine.begin() as conn:
                    await conn.run_sync(
                        lambda sync_conn: self.tables.metadata.create_all(
                            sync_conn,
                            tables=tables,
                            checkfirst=True,
                        )
                    )
        else:
            async with engine.connect() as conn:
                missing = await conn.run_sync(
                    lambda sync_conn: [
                        name
                        for name in (self.table_name, self.attempts_table_name)
                        if not sa.inspect(sync_conn).has_table(name)
                    ]
                )
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
            return encode_value(value)
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

    async def _submit(
        self,
        request: ExecutionRequest,
        encoded: dict[str, Any],
    ) -> Execution:
        await self._ensure_ready()
        execution_id = uuid4()
        try:
            async with self.engine.begin() as conn:
                now = await self._transaction_now(conn)
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
                    "available_at": now + timedelta(seconds=request.delay_s or 0),
                    "created_at": now,
                    "updated_at": now,
                    "version": 0,
                    "expires_at": request.expires_at,
                }
                await conn.execute(sa.insert(self.tables.executions).values(**values))
        except sa.exc.IntegrityError:
            if request.idempotency_key is None:
                raise
            async with self.engine.begin() as conn:
                row = (await conn.execute(
                    sa.select(self.tables.executions).where(
                        self.tables.executions.c.namespace == request.namespace,
                        self.tables.executions.c.task_name == request.task_name,
                        self.tables.executions.c.idempotency_key == request.idempotency_key,
                    )
                )).mappings().one_or_none()
            if row is None:
                raise
            if row["submission_digest"] != encoded["digest"]:
                raise ExecutionConflict(
                    "idempotency key was already used with a different submission"
                )
            return self._row_to_execution(row)
        return await self._get(request.namespace, execution_id)

    async def _get(self, namespace: str, execution_id: UUID) -> Execution | None:
        await self._ensure_ready()
        async with self.engine.begin() as conn:
            row = (await conn.execute(
                sa.select(self.tables.executions).where(
                    self.tables.executions.c.namespace == namespace,
                    self.tables.executions.c.id == execution_id,
                )
            )).mappings().one_or_none()
        return None if row is None else self._row_to_execution(row)

    async def _list(self, query: ExecutionQuery) -> ExecutionPage:
        await self._ensure_ready()
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
        async with self.engine.begin() as conn:
            rows = (await conn.execute(stmt)).mappings().all()
        has_more = len(rows) > query.limit
        selected = rows[: query.limit]
        items = tuple(self._row_to_execution(row) for row in selected)
        next_cursor = None
        if has_more and items:
            last = items[-1]
            next_cursor = self._encode_cursor(last.created_at, last.id)
        return ExecutionPage(items=items, next_cursor=next_cursor)

    async def _request_cancel(
        self,
        namespace: str,
        execution_id: UUID,
        reason: str | None,
    ) -> Execution | None:
        await self._ensure_ready()
        async with self.engine.begin() as conn:
            now = await self._transaction_now(conn)
            row = (await conn.execute(
                sa.select(self.tables.executions)
                .where(
                    self.tables.executions.c.namespace == namespace,
                    self.tables.executions.c.id == execution_id,
                )
                .with_for_update()
            )).mappings().one_or_none()
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
            await conn.execute(
                sa.update(self.tables.executions)
                .where(self.tables.executions.c.id == execution_id)
                .values(**values)
            )
            refreshed = (await conn.execute(
                sa.select(self.tables.executions).where(
                    self.tables.executions.c.id == execution_id
                )
            )).mappings().one()
        return self._row_to_execution(refreshed)

    async def _claim(
        self,
        namespace: str,
        task_names: tuple[str, ...],
        limit: int,
        lease_duration_s: float,
        worker_id: str,
    ) -> tuple[ExecutionLease, ...]:
        if not task_names or len(set(task_names)) != len(task_names):
            raise ValueError("task_names must be non-empty and unique")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be >= 1")
        if isinstance(lease_duration_s, bool) or lease_duration_s <= 0:
            raise ValueError("lease_duration_s must be > 0")
        if not isinstance(worker_id, str) or not worker_id.strip() or len(worker_id) > 255:
            raise ValueError("worker_id must be non-empty and <= 255 characters")
        await self._ensure_ready()
        executions = self.tables.executions
        attempts = self.tables.attempts
        lease_lost_retry = self._lease_lost_retry_predicate(executions, attempts)
        leases: list[ExecutionLease] = []
        async with self.engine.begin() as conn:
            now = await self._transaction_now(conn)
            await self._expire_queued(
                conn,
                now,
                limit=self.reclaim_batch_size,
            )
            await self._expire_cancel_requests(
                conn,
                attempts,
                now,
                limit=self.reclaim_batch_size,
            )
            await self._release_expired_leases(
                conn,
                attempts,
                now,
                limit=self.reclaim_batch_size,
            )
            stmt = (
                sa.select(executions)
                .where(
                    executions.c.namespace == namespace,
                    executions.c.task_name.in_(task_names),
                    executions.c.status.in_((
                        ExecutionStatus.QUEUED.value,
                        ExecutionStatus.RETRYING.value,
                    )),
                    executions.c.available_at <= now,
                    sa.or_(
                        executions.c.expires_at.is_(None),
                        executions.c.expires_at > now,
                        lease_lost_retry,
                    ),
                )
                .order_by(
                    executions.c.available_at,
                    executions.c.created_at,
                    executions.c.id,
                )
                .limit(limit)
            )
            try:
                stmt = stmt.with_for_update(skip_locked=True)
            except TypeError:
                stmt = stmt.with_for_update()
            rows = (await conn.execute(stmt)).mappings().all()
            for row in rows:
                attempt_id = uuid4()
                lease_token = uuid4()
                lease_expires_at = now + timedelta(seconds=lease_duration_s)
                attempt_no = row["attempts"] + 1
                await conn.execute(
                    sa.update(executions)
                    .where(executions.c.id == row["id"])
                    .values(
                        status=ExecutionStatus.RUNNING.value,
                        attempts=attempt_no,
                        lease_token=lease_token,
                        lease_expires_at=lease_expires_at,
                        worker_id=worker_id.strip(),
                        started_at=row["started_at"] or now,
                        updated_at=now,
                        version=row["version"] + 1,
                    )
                )
                await conn.execute(
                    sa.insert(attempts).values(
                        id=attempt_id,
                        execution_id=row["id"],
                        attempt_no=attempt_no,
                        lease_token=lease_token,
                        worker_id=worker_id.strip(),
                        status="running",
                        started_at=now,
                        heartbeat_at=now,
                    )
                )
                refreshed = (await conn.execute(
                    sa.select(executions).where(executions.c.id == row["id"])
                )).mappings().one()
                leases.append(
                    ExecutionLease(
                        execution=self._row_to_execution(refreshed),
                        attempt_id=attempt_id,
                        lease_token=lease_token,
                        lease_expires_at=_aware_utc(lease_expires_at),
                    )
                )
        return tuple(leases)

    async def _expire_queued(
        self,
        conn: Any,
        now: datetime,
        *,
        limit: int | None = None,
    ) -> None:
        executions = self.tables.executions
        attempts = self.tables.attempts
        lease_lost_retry = self._lease_lost_retry_predicate(executions, attempts)
        stmt = (
            sa.select(executions.c.id, executions.c.version)
            .where(
                executions.c.status.in_((
                    ExecutionStatus.QUEUED.value,
                    ExecutionStatus.RETRYING.value,
                )),
                executions.c.expires_at.is_not(None),
                executions.c.expires_at <= now,
                sa.not_(lease_lost_retry),
            )
            .order_by(executions.c.expires_at, executions.c.id)
            .limit(limit)
        )
        rows = (await conn.execute(stmt)).all()
        for row in rows:
            await conn.execute(
                sa.update(executions)
                .where(
                    executions.c.id == row.id,
                    executions.c.version == row.version,
                    executions.c.status.in_((
                        ExecutionStatus.QUEUED.value,
                        ExecutionStatus.RETRYING.value,
                    )),
                    executions.c.expires_at.is_not(None),
                    executions.c.expires_at <= now,
                    sa.not_(lease_lost_retry),
                )
                .values(
                    status=ExecutionStatus.EXPIRED.value,
                    finished_at=now,
                    updated_at=now,
                    version=executions.c.version + 1,
                )
            )

    async def _expire_cancel_requests(
        self,
        conn: Any,
        attempts: sa.Table,
        now: datetime,
        *,
        limit: int | None = None,
    ) -> None:
        executions = self.tables.executions
        stmt = (
            sa.select(
                executions.c.id,
                executions.c.lease_token,
                executions.c.version,
            )
            .where(
                executions.c.status == ExecutionStatus.CANCEL_REQUESTED.value,
                executions.c.lease_expires_at.is_not(None),
                executions.c.lease_expires_at <= now,
            )
            .order_by(executions.c.lease_expires_at, executions.c.id)
            .limit(limit)
        )
        rows = (await conn.execute(stmt)).all()
        for row in rows:
            updated = await conn.execute(
                sa.update(executions)
                .where(
                    executions.c.id == row.id,
                    executions.c.version == row.version,
                    executions.c.lease_token == row.lease_token,
                    executions.c.status == ExecutionStatus.CANCEL_REQUESTED.value,
                    executions.c.lease_expires_at.is_not(None),
                    executions.c.lease_expires_at <= now,
                )
                .values(
                    status=ExecutionStatus.CANCELLED.value,
                    finished_at=now,
                    lease_token=None,
                    lease_expires_at=None,
                    worker_id=None,
                    updated_at=now,
                    version=executions.c.version + 1,
                )
            )
            if updated.rowcount != 1:
                continue
            await conn.execute(
                sa.update(attempts)
                .where(
                    attempts.c.execution_id == row.id,
                    attempts.c.lease_token == row.lease_token,
                    attempts.c.status == "running",
                )
                .values(status="cancelled", finished_at=now)
            )

    async def _release_expired_leases(
        self,
        conn: Any,
        attempts: sa.Table,
        now: datetime,
        *,
        limit: int | None = None,
    ) -> None:
        executions = self.tables.executions
        stmt = (
            sa.select(
                executions.c.id,
                executions.c.lease_token,
                executions.c.version,
            )
            .where(
                executions.c.status == ExecutionStatus.RUNNING.value,
                executions.c.lease_expires_at.is_not(None),
                executions.c.lease_expires_at <= now,
            )
            .order_by(executions.c.lease_expires_at, executions.c.id)
            .limit(limit)
        )
        rows = (await conn.execute(stmt)).all()
        for row in rows:
            values = {
                "status": ExecutionStatus.RETRYING.value,
                "available_at": now,
            }
            values.update(
                {
                    "lease_token": None,
                    "lease_expires_at": None,
                    "worker_id": None,
                    "updated_at": now,
                    "version": executions.c.version + 1,
                }
            )
            updated = await conn.execute(
                sa.update(executions)
                .where(
                    executions.c.id == row.id,
                    executions.c.version == row.version,
                    executions.c.lease_token == row.lease_token,
                    executions.c.status == ExecutionStatus.RUNNING.value,
                    executions.c.lease_expires_at.is_not(None),
                    executions.c.lease_expires_at <= now,
                )
                .values(**values)
            )
            if updated.rowcount != 1:
                continue
            await conn.execute(
                sa.update(attempts)
                .where(
                    attempts.c.execution_id == row.id,
                    attempts.c.lease_token == row.lease_token,
                    attempts.c.status == "running",
                )
                .values(status="lease_lost", finished_at=now)
            )

    def _lease_lost_retry_predicate(
        self,
        executions: sa.Table,
        attempts: sa.Table,
    ) -> Any:
        return sa.and_(
            executions.c.status == ExecutionStatus.RETRYING.value,
            sa.exists().where(
                attempts.c.execution_id == executions.c.id,
                attempts.c.attempt_no == executions.c.attempts,
                attempts.c.status == "lease_lost",
            ),
        )

    async def _heartbeat(
        self,
        execution_id: UUID,
        attempt_id: UUID,
        lease_token: UUID,
        lease_duration_s: float,
    ) -> HeartbeatResult:
        if lease_duration_s <= 0:
            raise ValueError("lease_duration_s must be > 0")
        await self._ensure_ready()
        executions = self.tables.executions
        attempts = self.tables.attempts
        async with self.engine.begin() as conn:
            now = await self._transaction_now(conn)
            expires = now + timedelta(seconds=lease_duration_s)
            updated = await conn.execute(
                sa.update(executions)
                .where(
                    executions.c.id == execution_id,
                    executions.c.lease_token == lease_token,
                    executions.c.status.in_((
                        ExecutionStatus.RUNNING.value,
                        ExecutionStatus.CANCEL_REQUESTED.value,
                    )),
                    executions.c.lease_expires_at.is_not(None),
                    executions.c.lease_expires_at > now,
                )
                .values(lease_expires_at=expires, updated_at=now)
            )
            attempt_updated = await conn.execute(
                sa.update(attempts)
                .where(
                    attempts.c.id == attempt_id,
                    attempts.c.execution_id == execution_id,
                    attempts.c.lease_token == lease_token,
                    attempts.c.status == "running",
                )
                .values(heartbeat_at=now)
            )
            if updated.rowcount != 1 or attempt_updated.rowcount != 1:
                raise StaleExecutionLease("execution lease is no longer valid")
            row = (await conn.execute(
                sa.select(executions.c.status).where(executions.c.id == execution_id)
            )).one()
        return HeartbeatResult(
            lease_expires_at=_aware_utc(expires),
            cancel_requested=row.status == ExecutionStatus.CANCEL_REQUESTED.value,
        )

    async def _complete(
        self,
        execution_id: UUID,
        attempt_id: UUID,
        lease_token: UUID,
        completion: ExecutionCompletion,
    ) -> Execution:
        await self._ensure_ready()
        encoded_result = None
        error = None if completion.error is None else asdict(completion.error)
        executions = self.tables.executions
        attempts = self.tables.attempts
        expected_statuses = {
            ExecutionStatus.SUCCEEDED: (
                ExecutionStatus.RUNNING.value,
                ExecutionStatus.CANCEL_REQUESTED.value,
            ),
            ExecutionStatus.RETRYING: (ExecutionStatus.RUNNING.value,),
            ExecutionStatus.FAILED: (ExecutionStatus.RUNNING.value,),
            ExecutionStatus.CANCELLED: (
                ExecutionStatus.RUNNING.value,
                ExecutionStatus.CANCEL_REQUESTED.value,
            ),
        }[completion.status]
        async with self.engine.begin() as conn:
            now = await self._transaction_now(conn)
            current = (await conn.execute(
                sa.select(executions).where(executions.c.id == execution_id).with_for_update()
            )).mappings().one_or_none()
            if current is None:
                raise StaleExecutionLease("execution does not exist")

            cancel_won = (
                completion.status is ExecutionStatus.SUCCEEDED
                and current["status"] == ExecutionStatus.CANCEL_REQUESTED.value
            )
            if completion.status is ExecutionStatus.SUCCEEDED and not cancel_won:
                encoded_result = self._encode_value(completion.result, "result")
                if self._encoded_size(encoded_result) > self.max_result_bytes:
                    raise ExecutionEncodingError(
                        "execution result exceeds the configured limit"
                    )
            effective_status = (
                ExecutionStatus.CANCELLED if cancel_won else completion.status
            )
            effective_result = None if cancel_won else encoded_result
            effective_error = None if cancel_won else error
            values: dict[str, Any] = {
                "status": effective_status.value,
                "updated_at": now,
                "version": executions.c.version + 1,
                "lease_token": None,
                "lease_expires_at": None,
                "worker_id": None,
                "result": effective_result,
                "error": effective_error,
            }
            if effective_status is ExecutionStatus.RETRYING:
                values.update(
                    {
                        "available_at": now + timedelta(seconds=completion.delay_s or 0),
                        "finished_at": None,
                    }
                )
            else:
                values["finished_at"] = now
            attempt_status = {
                ExecutionStatus.SUCCEEDED: "succeeded",
                ExecutionStatus.RETRYING: "retrying",
                ExecutionStatus.FAILED: "failed",
                ExecutionStatus.CANCELLED: "cancelled",
            }[effective_status]
            updated = await conn.execute(
                sa.update(executions)
                .where(
                    executions.c.id == execution_id,
                    executions.c.lease_token == lease_token,
                    executions.c.status.in_(expected_statuses),
                    executions.c.lease_expires_at.is_not(None),
                    executions.c.lease_expires_at > now,
                )
                .values(**values)
            )
            if updated.rowcount != 1:
                current = (await conn.execute(
                    sa.select(executions).where(executions.c.id == execution_id)
                )).mappings().one_or_none()
                if current is None:
                    raise StaleExecutionLease("execution does not exist")
                same_terminal_status = (
                    current["status"] == completion.status.value
                    and current["status"] in {
                        status.value for status in (
                            ExecutionStatus.SUCCEEDED,
                            ExecutionStatus.FAILED,
                            ExecutionStatus.CANCELLED,
                        )
                    }
                )
                expected_result = (
                    encoded_result
                    if completion.status is ExecutionStatus.SUCCEEDED
                    else None
                )
                # A failed CAS may be an idempotent replay, but only the exact
                # persisted business result/error is safe to accept.
                if (
                    same_terminal_status
                    and current["result"] == expected_result
                    and current["error"] == error
                ):
                    return self._row_to_execution(current)
                raise StaleExecutionLease("execution lease is no longer valid")
            attempt_updated = await conn.execute(
                sa.update(attempts)
                .where(
                    attempts.c.id == attempt_id,
                    attempts.c.execution_id == execution_id,
                    attempts.c.lease_token == lease_token,
                    attempts.c.status == "running",
                )
                .values(status=attempt_status, error=effective_error, finished_at=now)
            )
            if attempt_updated.rowcount != 1:
                raise StaleExecutionLease("execution attempt is no longer valid")
            row = (await conn.execute(
                sa.select(executions).where(executions.c.id == execution_id)
            )).mappings().one()
        return self._row_to_execution(row)

    async def _release(
        self,
        execution_id: UUID,
        attempt_id: UUID,
        lease_token: UUID,
    ) -> Execution:
        await self._ensure_ready()
        executions = self.tables.executions
        attempts = self.tables.attempts
        async with self.engine.begin() as conn:
            now = await self._transaction_now(conn)
            current = (await conn.execute(
                sa.select(executions).where(executions.c.id == execution_id)
            )).mappings().one_or_none()
            if current is None:
                raise StaleExecutionLease("execution does not exist")
            updated = await conn.execute(
                sa.update(executions)
                .where(
                    executions.c.id == execution_id,
                    executions.c.lease_token == lease_token,
                    executions.c.status == ExecutionStatus.RUNNING.value,
                    executions.c.lease_expires_at.is_not(None),
                    executions.c.lease_expires_at > now,
                )
                .values(
                    status=ExecutionStatus.QUEUED.value,
                    available_at=now,
                    lease_token=None,
                    lease_expires_at=None,
                    worker_id=None,
                    updated_at=now,
                    version=executions.c.version + 1,
                )
            )
            if updated.rowcount != 1:
                raise StaleExecutionLease("execution lease is no longer valid")
            attempt_updated = await conn.execute(
                sa.update(attempts)
                .where(
                    attempts.c.id == attempt_id,
                    attempts.c.execution_id == execution_id,
                    attempts.c.lease_token == lease_token,
                    attempts.c.status == "running",
                )
                .values(status="lease_lost", finished_at=now)
            )
            if attempt_updated.rowcount != 1:
                raise StaleExecutionLease("execution attempt is no longer valid")
            row = (await conn.execute(
                sa.select(executions).where(executions.c.id == execution_id)
            )).mappings().one()
        return self._row_to_execution(row)

    def _row_to_execution(self, row: Mapping[str, Any]) -> Execution:
        try:
            raw_error = decode_value(row["error"]) if row["error"] is not None else None
            error = None if raw_error is None else ExecutionErrorDetail(**raw_error)
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
            if len(cursor) > 1024 or any(char.isspace() for char in cursor):
                raise ValueError("invalid execution cursor")
            padded = cursor + "=" * (-len(cursor) % 4)
            raw = json.loads(
                base64.b64decode(
                    padded.encode("ascii"),
                    altchars=b"-_",
                    validate=True,
                )
            )
            if not isinstance(raw, dict) or set(raw) != {"v", "created_at", "id"}:
                raise ValueError("invalid execution cursor")
            if isinstance(raw["v"], bool) or not isinstance(raw["v"], int):
                raise ValueError("invalid execution cursor")
            if raw["v"] != 1:
                raise ValueError("unknown cursor version")
            if not isinstance(raw["created_at"], str) or not isinstance(raw["id"], str):
                raise ValueError("invalid execution cursor")
            created_at = datetime.fromisoformat(raw["created_at"])
            if created_at.tzinfo is None or created_at.utcoffset() is None:
                raise ValueError("cursor created_at must be timezone-aware")
            created_at = created_at.astimezone(timezone.utc)
            execution_id = UUID(raw["id"])
            if cursor != PostgresExecutionBackend._encode_cursor(created_at, execution_id):
                raise ValueError("invalid execution cursor")
            return created_at, execution_id
        except ValueError as exc:
            if str(exc) == "unknown cursor version":
                raise
            raise ValueError("invalid execution cursor") from exc
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
