from __future__ import annotations

import asyncio
import copy
import math
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from onestep.connectors.base import Delivery, Source
from onestep.envelope import Envelope
from onestep.execution import (
    ExecutionCompletion,
    ExecutionError,
    ExecutionStatus,
)
from onestep.resilience import (
    ConnectorOperation,
    ConnectorOperationError,
    is_retryable_connector_error,
)

from .execution_backend import (
    ExecutionLease,
    HeartbeatResult,
    PostgresExecutionBackend,
)
from .resilience import as_postgres_connector_operation_error


def _connector_secret_tokens(connector: Any) -> list[str]:
    public = getattr(connector, "secret_tokens", None)
    if callable(public):
        return public()
    private = getattr(connector, "_secret_tokens", None)
    return private() if callable(private) else []


def _validate_execution_source_options(
    *,
    namespace: str,
    task_names: Sequence[str],
    batch_size: int,
    poll_interval_s: float,
    lease_duration_s: float,
    heartbeat_interval_s: float,
    worker_id: str,
    field_prefix: str = "",
) -> tuple[str, ...]:
    prefix = f"{field_prefix}." if field_prefix else ""
    if not isinstance(namespace, str) or not namespace.strip() or len(namespace.strip()) > 255:
        raise ValueError(f"{prefix}namespace must be non-empty and <= 255 characters")
    if not isinstance(task_names, Sequence) or isinstance(task_names, (str, bytes)):
        raise TypeError(f"{prefix}task_names must be a sequence of strings")
    normalized_tasks = tuple(
        task.strip() if isinstance(task, str) else task for task in task_names
    )
    if not normalized_tasks or any(
        not isinstance(task, str) or not task or len(task) > 255
        for task in normalized_tasks
    ):
        raise ValueError(f"{prefix}task_names must be non-empty strings <= 255 characters")
    if len(normalized_tasks) != 1:
        raise ValueError(f"{prefix}task_names must contain exactly one task name")
    if len(set(normalized_tasks)) != len(normalized_tasks):
        raise ValueError(f"{prefix}task_names must be unique")
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size < 1:
        raise ValueError(f"{prefix}batch_size must be >= 1")
    for name, value in (
        ("poll_interval_s", poll_interval_s),
        ("lease_duration_s", lease_duration_s),
        ("heartbeat_interval_s", heartbeat_interval_s),
    ):
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ValueError(f"{prefix}{name} must be a finite number")
    if poll_interval_s <= 0:
        raise ValueError(f"{prefix}poll_interval_s must be > 0")
    if lease_duration_s <= 0:
        raise ValueError(f"{prefix}lease_duration_s must be > 0")
    if (
        heartbeat_interval_s <= 0
        or (
            heartbeat_interval_s > lease_duration_s / 3
            and not math.isclose(heartbeat_interval_s, lease_duration_s / 3)
        )
    ):
        raise ValueError(
            f"{prefix}heartbeat_interval_s must be > 0 and <= "
            f"{prefix}lease_duration_s / 3"
        )
    if not isinstance(worker_id, str) or not worker_id.strip() or len(worker_id.strip()) > 255:
        raise ValueError(f"{prefix}worker_id must be non-empty and <= 255 characters")
    return normalized_tasks


class PostgresExecutionSource(Source):
    fetch_is_cancel_safe = False

    def __init__(
        self,
        *,
        backend: PostgresExecutionBackend,
        namespace: str,
        task_names: Sequence[str],
        batch_size: int = 100,
        poll_interval_s: float = 1.0,
        lease_duration_s: float = 90.0,
        heartbeat_interval_s: float = 30.0,
        worker_id: str = "onestep-worker",
    ) -> None:
        normalized_tasks = _validate_execution_source_options(
            namespace=namespace,
            task_names=task_names,
            batch_size=batch_size,
            poll_interval_s=poll_interval_s,
            lease_duration_s=lease_duration_s,
            heartbeat_interval_s=heartbeat_interval_s,
            worker_id=worker_id,
        )
        super().__init__(f"postgres.execution:{namespace.strip()}")
        self.backend = backend
        self.namespace = namespace.strip()
        self.task_names = normalized_tasks
        self.task_name = normalized_tasks[0]
        self.batch_size = batch_size
        self.poll_interval_s = float(poll_interval_s)
        self.lease_duration_s = float(lease_duration_s)
        self.heartbeat_interval_s = float(heartbeat_interval_s)
        self.worker_id = worker_id.strip()

    def validate_task(self, task_name: str) -> None:
        if task_name != self.task_name:
            raise ValueError(
                f"{self.name} is configured for task {self.task_name!r}, "
                f"not {task_name!r}"
            )

    async def open(self) -> None:
        await self.backend.open()

    async def fetch(self, limit: int) -> list[Delivery]:
        try:
            leases = await self.backend.claim(
                self.namespace,
                self.task_names,
                min(limit, self.batch_size),
                self.lease_duration_s,
                self.worker_id,
            )
        except Exception as exc:
            connector_error = as_postgres_connector_operation_error(
                operation=ConnectorOperation.FETCH,
                exc=exc,
                source_name=self.name,
                retry_delay_s=self.poll_interval_s,
                secrets=_connector_secret_tokens(self.backend.connector),
            )
            if connector_error is None:
                raise
            raise connector_error from None
        deliveries: list[Delivery] = []
        for lease in leases:
            metadata = copy.deepcopy(dict(lease.execution.metadata))
            metadata["onestep.execution"] = {
                "id": str(lease.execution.id),
                "attempt_id": str(lease.attempt_id),
            }
            deliveries.append(
                PostgresExecutionDelivery(
                    source=self,
                    lease=lease,
                    envelope=Envelope(
                        body=copy.deepcopy(lease.execution.payload),
                        meta=metadata,
                        attempts=max(0, lease.execution.attempts - 1),
                    ),
                )
            )
        return deliveries

    async def close(self) -> None:
        return None


class PostgresExecutionDelivery(Delivery):
    def __init__(
        self,
        *,
        source: PostgresExecutionSource,
        lease: ExecutionLease,
        envelope: Envelope,
    ) -> None:
        super().__init__(envelope)
        self.source = source
        self.lease = lease
        self.execution_id = lease.execution.id
        self.attempt_id = lease.attempt_id
        self.lease_token = lease.lease_token
        self.lease_expires_at = lease.lease_expires_at
        self.cancel_requested = False
        self._owner_task: asyncio.Task[Any] | None = None
        self._heartbeat_task: asyncio.Task[Any] | None = None
        self._heartbeat_stop = asyncio.Event()
        self._completed = False

    async def start_processing(self) -> None:
        if self._heartbeat_task is not None:
            return
        self._owner_task = asyncio.current_task()
        if self._owner_task is None:
            raise RuntimeError("execution delivery must start inside an asyncio task")
        self._heartbeat_stop.clear()
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def _heartbeat_loop(self) -> None:
        while not self._heartbeat_stop.is_set():
            try:
                await asyncio.wait_for(
                    self._heartbeat_stop.wait(),
                    timeout=self.source.heartbeat_interval_s,
                )
                return
            except asyncio.TimeoutError:
                pass
            result = await self._heartbeat_with_retry()
            if result is None:
                self._heartbeat_stop.set()
                self._cancel_owner()
                return
            self._apply_heartbeat_result(result)
            if result.cancel_requested:
                self._heartbeat_stop.set()
                self._cancel_owner()
                return

    async def _heartbeat_with_retry(self) -> HeartbeatResult | None:
        for attempt in range(3):
            try:
                return await self.source.backend.heartbeat(
                    self.execution_id,
                    self.attempt_id,
                    self.lease_token,
                    self.source.lease_duration_s,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                connector_error = (
                    exc
                    if isinstance(exc, ConnectorOperationError)
                    else as_postgres_connector_operation_error(
                        operation=ConnectorOperation.HEARTBEAT,
                        exc=exc,
                        source_name=self.source.name,
                        retry_delay_s=self.source.heartbeat_interval_s,
                        secrets=_connector_secret_tokens(self.source.backend.connector),
                    )
                )
                if connector_error is None or not is_retryable_connector_error(connector_error):
                    return None
                remaining = (
                    self.lease_expires_at - datetime.now(timezone.utc)
                ).total_seconds()
                if attempt == 2 or remaining <= 0:
                    return None
                # Keep each retry inside the remaining lease; the caller cancels
                # the owner when no retry can be made before the deadline.
                delay_s = min(
                    self.source.heartbeat_interval_s * (2**attempt),
                    remaining / 4,
                )
                try:
                    await asyncio.wait_for(self._heartbeat_stop.wait(), timeout=delay_s)
                    return None
                except asyncio.TimeoutError:
                    continue
        return None

    def _apply_heartbeat_result(self, result: HeartbeatResult) -> None:
        self.lease_expires_at = result.lease_expires_at
        self.cancel_requested = result.cancel_requested

    def _cancel_owner(self) -> None:
        if self._owner_task is not None and not self._owner_task.done():
            self._owner_task.cancel()

    async def _stop_heartbeat(self) -> None:
        self._heartbeat_stop.set()
        task = self._heartbeat_task
        self._heartbeat_task = None
        if task is None or task is asyncio.current_task():
            return
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def complete_execution(self, completion: ExecutionCompletion) -> None:
        await self._stop_heartbeat()
        if self._completed:
            return
        completed = await self.source.backend.complete(
            self.execution_id,
            self.attempt_id,
            self.lease_token,
            completion,
        )
        self._completed = True
        if (
            completion.status is ExecutionStatus.SUCCEEDED
            and completed.status is ExecutionStatus.CANCELLED
        ):
            self.cancel_requested = True
            raise asyncio.CancelledError

    async def release_unstarted(self) -> None:
        await self.source.backend.release(
            self.execution_id,
            self.attempt_id,
            self.lease_token,
        )

    async def ack(self) -> None:
        # Delivery.ack() has no result argument. Runtime-managed execution uses
        # complete_execution() so handler results are persisted before success.
        await self.complete_execution(
            ExecutionCompletion(status=ExecutionStatus.SUCCEEDED)
        )

    async def retry(self, *, delay_s: float | None = None) -> None:
        await self.complete_execution(
            ExecutionCompletion(
                status=ExecutionStatus.RETRYING,
                delay_s=delay_s,
            )
        )

    async def fail(self, exc: Exception | None = None) -> None:
        await self.complete_execution(
            ExecutionCompletion(
                status=ExecutionStatus.FAILED,
                error=(
                    None
                    if exc is None
                    else ExecutionError(
                        kind="error",
                        exception_type=type(exc).__name__,
                    )
                ),
            )
        )


__all__ = [
    "PostgresExecutionDelivery",
    "PostgresExecutionSource",
]
