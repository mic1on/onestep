from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

SessionFactory = Callable[[], Session]
_LOGGER = logging.getLogger(__name__)

# PostgreSQL advisory lock ID used for leader election.
_ADVISORY_LOCK_ID = 20260515001


@dataclass
class LeaderState:
    is_leader: bool = False
    acquired_at: datetime | None = None
    last_renewed_at: datetime | None = None
    expires_at: datetime | None = None
    held_lock_id: int | None = None
    release_on_exit: bool = False


class PostgresAdvisoryLeader:
    """Leader election using PostgreSQL session-level advisory locks.

    Each API replica tries to acquire the same advisory lock. Only one
    replica holds the lock at a time, providing single-owner execution
    for background workers across multiple replicas.

    The lock is automatically released when the database session or
    connection is closed, so crashed replicas do not hold the lock forever.
    """

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory
        self._state = LeaderState()

    @property
    def state(self) -> LeaderState:
        return self._state

    def try_acquire(
        self,
        *,
        lock_id: int = _ADVISORY_LOCK_ID,
        ttl_s: int = 30,
    ) -> bool:
        """Try to acquire the leader lock.

        Uses pg_try_advisory_lock which returns immediately (non-blocking).
        The lock is held at the session level until the session is closed
        or pg_advisory_unlock is called.

        Returns True if this replica became the leader.
        """
        try:
            with self._session_factory() as session:
                result = session.execute(
                    text("SELECT pg_try_advisory_lock(:lock_id)"),
                    {"lock_id": lock_id},
                ).scalar()
                acquired = bool(result)
                if acquired:
                    now = datetime.now(UTC)
                    self._state.is_leader = True
                    self._state.acquired_at = now
                    self._state.last_renewed_at = now
                    self._state.expires_at = now + timedelta(seconds=ttl_s)
                    self._state.held_lock_id = lock_id
                    self._state.release_on_exit = True
                    _LOGGER.info(
                        "leader lock acquired",
                        extra={
                            "lock_id": lock_id,
                            "ttl_s": ttl_s,
                        },
                    )
                else:
                    _LOGGER.debug("leader lock not acquired (held by another replica)")
                return acquired
        except Exception as exc:
            _LOGGER.warning(
                "leader lock acquisition failed",
                extra={"lock_id": lock_id, "error": str(exc)},
            )
            return False

    def renew(self, *, ttl_s: int = 30) -> bool:
        """Renew the leader lease by updating the expiry timestamp.

        This does not re-acquire the lock; it just bumps the in-memory
        heartbeat so readiness checks know the leader is still active.
        """
        if not self._state.is_leader:
            return False
        now = datetime.now(UTC)
        self._state.last_renewed_at = now
        self._state.expires_at = now + timedelta(seconds=ttl_s)
        return True

    def release(self) -> bool:
        """Release the advisory lock explicitly.

        Called during graceful shutdown so another replica can take over.
        """
        if not self._state.is_leader or not self._state.release_on_exit:
            return False
        lock_id = self._state.held_lock_id
        released = False
        try:
            with self._session_factory() as session:
                result = session.execute(
                    text("SELECT pg_advisory_unlock(:lock_id)"),
                    {"lock_id": lock_id},
                ).scalar()
                released = bool(result)
        except SQLAlchemyError as exc:
            _LOGGER.warning(
                "leader lock release failed",
                extra={"lock_id": lock_id, "error": str(exc)},
            )
        if released:
            _LOGGER.info("leader lock released", extra={"lock_id": lock_id})
        self._state = LeaderState()
        return released

    def is_still_leader(self) -> bool:
        """Check if this replica still holds the lock without releasing it.

        Uses pg_try_advisory_lock with the same lock ID. If it returns
        true, we already hold the lock (re-acquired no-op) and are still
        the leader. If it returns false, someone else holds it.
        """
        if not self._state.is_leader:
            return False
        lock_id = self._state.held_lock_id
        if lock_id is None:
            return False
        try:
            with self._session_factory() as session:
                result = session.execute(
                    text("SELECT pg_try_advisory_lock(:lock_id)"),
                    {"lock_id": lock_id},
                ).scalar()
                still_leader = bool(result)
                if still_leader:
                    session.execute(
                        text("SELECT pg_advisory_unlock(:lock_id)"),
                        {"lock_id": lock_id},
                    )
                return still_leader
        except SQLAlchemyError:
            return False


class NullLeader:
    """Fallback leader that always reports as leader.

    Used when PostgreSQL advisory locks are not available (e.g., SQLite).
    """

    def __init__(self) -> None:
        self._state = LeaderState(is_leader=True)

    @property
    def state(self) -> LeaderState:
        return self._state

    def try_acquire(self, **kwargs) -> bool:
        self._state.is_leader = True
        now = datetime.now(UTC)
        self._state.acquired_at = now
        self._state.last_renewed_at = now
        self._state.expires_at = now + timedelta(seconds=kwargs.get("ttl_s", 30))
        return True

    def renew(self, **kwargs) -> bool:
        now = datetime.now(UTC)
        self._state.last_renewed_at = now
        self._state.expires_at = now + timedelta(seconds=kwargs.get("ttl_s", 30))
        return True

    def release(self) -> bool:
        self._state = LeaderState()
        return True

    def is_still_leader(self) -> bool:
        return self._state.is_leader


def create_leader(session_factory: SessionFactory) -> PostgresAdvisoryLeader | NullLeader:
    """Create the appropriate leader election implementation based on the database type."""
    return PostgresAdvisoryLeader(session_factory)