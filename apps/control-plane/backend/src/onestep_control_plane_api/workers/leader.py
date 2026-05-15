from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger("onestep_control_plane_api.workers.leader")


class WorkerLeaseError(RuntimeError):
    """Raised when a worker cannot validate or acquire its lease."""


class WorkerLease:
    mode = "unknown"
    acquired_at: datetime | None = None

    def ensure_leader(self) -> bool:
        raise NotImplementedError

    def release(self) -> None:
        raise NotImplementedError


class LocalWorkerLease(WorkerLease):
    mode = "local"

    def ensure_leader(self) -> bool:
        if self.acquired_at is None:
            self.acquired_at = datetime.now(UTC)
        return True

    def release(self) -> None:
        self.acquired_at = None


class PostgresAdvisoryLockLease(WorkerLease):
    mode = "postgres_advisory_lock"

    def __init__(self, *, engine: Engine, lock_key: int, worker_name: str) -> None:
        self._engine = engine
        self._lock_key = lock_key
        self._worker_name = worker_name
        self._connection: Connection | None = None
        self.acquired_at: datetime | None = None

    def ensure_leader(self) -> bool:
        if self._connection is not None:
            try:
                self._connection.execute(text("SELECT 1"))
                self._connection.commit()
            except SQLAlchemyError as exc:
                self.release()
                raise WorkerLeaseError(
                    f"{self._worker_name} lost advisory lock connection"
                ) from exc
            return True

        connection = self._engine.connect()
        try:
            acquired = bool(
                connection.execute(
                    text("SELECT pg_try_advisory_lock(:lock_key)"),
                    {"lock_key": self._lock_key},
                ).scalar()
            )
            connection.commit()
        except SQLAlchemyError as exc:
            connection.rollback()
            connection.close()
            raise WorkerLeaseError(
                f"{self._worker_name} failed to acquire advisory lock"
            ) from exc

        if not acquired:
            connection.close()
            return False

        self._connection = connection
        self.acquired_at = datetime.now(UTC)
        return True

    def release(self) -> None:
        connection = self._connection
        self._connection = None
        self.acquired_at = None
        if connection is None:
            return

        try:
            connection.execute(
                text("SELECT pg_advisory_unlock(:lock_key)"),
                {"lock_key": self._lock_key},
            )
            connection.commit()
        except SQLAlchemyError:
            logger.warning(
                "failed to release advisory lock",
                extra={
                    "worker_name": self._worker_name,
                    "lock_key": self._lock_key,
                },
                exc_info=True,
            )
            connection.rollback()
        finally:
            connection.close()


def create_worker_lease(
    *,
    engine: Engine | None,
    lock_key: int,
    worker_name: str,
) -> WorkerLease:
    if engine is not None and engine.dialect.name == "postgresql":
        return PostgresAdvisoryLockLease(
            engine=engine,
            lock_key=lock_key,
            worker_name=worker_name,
        )
    return LocalWorkerLease()
