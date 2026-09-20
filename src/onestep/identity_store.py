from __future__ import annotations

import errno
import json
import os
import socket
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

STATE_SCHEMA_VERSION = 1
STATE_FILENAME = "identity.json"
LOCK_FILENAME = "identity.lock"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _format_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def derive_replica_instance_id(
    *,
    service_name: str,
    environment: str,
    replica_key: str,
) -> UUID:
    identity_key = f"https://onestep/runtime-instance/{environment}/{service_name}/{replica_key}"
    return uuid5(NAMESPACE_URL, identity_key)


def build_default_state_dir(
    *,
    service_name: str,
    environment: str,
    replica_key: str | None = None,
) -> str:
    base_path = Path.home() / ".onestep" / "control-plane-state"
    resolved = base_path / _safe_path_component(environment) / _safe_path_component(service_name)
    if replica_key:
        resolved = resolved / _safe_path_component(replica_key)
    return str(resolved)


@dataclass
class _IdentityState:
    instance_id: UUID
    heartbeat_sequence: int
    sync_sequence: int
    created_at: datetime
    updated_at: datetime

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": STATE_SCHEMA_VERSION,
            "instance_id": str(self.instance_id),
            "heartbeat_sequence": self.heartbeat_sequence,
            "sync_sequence": self.sync_sequence,
            "created_at": _format_datetime(self.created_at),
            "updated_at": _format_datetime(self.updated_at),
        }


class IdentityStateError(RuntimeError):
    pass


class IdentityLockError(RuntimeError):
    pass


def peek_instance_id(state_dir: str | os.PathLike[str]) -> UUID | None:
    state_path = Path(state_dir) / STATE_FILENAME
    if not state_path.exists():
        return None
    return _read_state(state_path).instance_id


def _safe_path_component(value: str) -> str:
    normalized = value.strip() or "default"
    return "".join(
        char if char.isalnum() or char in {"-", "_", "."} else "_" for char in normalized
    )


class IdentityStore:
    def __init__(
        self,
        state_dir: str | os.PathLike[str],
        *,
        instance_id: UUID | None = None,
    ) -> None:
        self.state_dir = Path(state_dir)
        self.state_path = self.state_dir / STATE_FILENAME
        self.lock_path = self.state_dir / LOCK_FILENAME
        self._lock_fd: int | None = None
        self._closed = False
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._acquire_lock()
        try:
            self._state = self._load_state(instance_id=instance_id)
        except BaseException:
            self.close()
            raise

    def __enter__(self) -> IdentityStore:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    @property
    def instance_id(self) -> UUID:
        return self._state.instance_id

    def load_or_create_instance_id(self) -> UUID:
        return self._state.instance_id

    def next_heartbeat_sequence(self) -> int:
        self._state.heartbeat_sequence += 1
        self._persist_state()
        return self._state.heartbeat_sequence

    def next_sync_sequence(self) -> int:
        self._state.sync_sequence += 1
        self._persist_state()
        return self._state.sync_sequence

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._lock_fd is not None:
            # Closing releases the OS lock. Never unlink: other contenders may
            # already have this inode open.
            os.close(self._lock_fd)
            self._lock_fd = None

    def _load_state(self, *, instance_id: UUID | None) -> _IdentityState:
        if self.state_path.exists():
            state = _read_state(self.state_path)
        else:
            now = _utcnow()
            state = _IdentityState(
                instance_id=uuid4(),
                heartbeat_sequence=0,
                sync_sequence=0,
                created_at=now,
                updated_at=now,
            )
        if instance_id is not None and state.instance_id != instance_id:
            state.instance_id = instance_id
        self._write_state(state)
        return state

    def _persist_state(self) -> None:
        self._state.updated_at = _utcnow()
        self._write_state(self._state)

    def _write_state(self, state: _IdentityState) -> None:
        _atomic_write_json(self.state_path, state.to_payload())

    def _acquire_lock(self) -> None:
        lock_payload = {
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
            "created_at": _format_datetime(_utcnow()),
        }
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                _lock_file(fd)
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                    raise
                raise IdentityLockError(
                    f"identity state dir {self.state_dir} is already locked"
                ) from exc
            # Only the lock owner can replace the diagnostic payload. Metadata
            # is never used to decide liveness (PIDs can be reused).
            payload = json.dumps(lock_payload, sort_keys=True).encode("utf-8")
            os.lseek(fd, 0, os.SEEK_SET)
            offset = 0
            while offset < len(payload):
                offset += os.write(fd, payload[offset:])
            os.ftruncate(fd, len(payload))
            os.fsync(fd)
        except BaseException:
            os.close(fd)
            raise
        self._lock_fd = fd


def _lock_file(fd: int) -> None:
    if sys.platform == "win32":
        import msvcrt

        # Windows permits locking a byte beyond EOF for a newly created file.
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _read_state(path: Path) -> _IdentityState:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise IdentityStateError(f"identity state file {path} does not exist") from exc
    except json.JSONDecodeError as exc:
        raise IdentityStateError(f"identity state file {path} is not valid JSON") from exc

    if payload.get("schema_version") != STATE_SCHEMA_VERSION:
        raise IdentityStateError(
            f"identity state file {path} has unsupported schema_version={payload.get('schema_version')}"
        )

    try:
        return _IdentityState(
            instance_id=UUID(str(payload["instance_id"])),
            heartbeat_sequence=int(payload.get("heartbeat_sequence", 0)),
            sync_sequence=int(payload.get("sync_sequence", 0)),
            created_at=_parse_datetime(str(payload["created_at"])),
            updated_at=_parse_datetime(str(payload["updated_at"])),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise IdentityStateError(f"identity state file {path} is missing required fields") from exc


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


__all__ = [
    "IdentityLockError",
    "IdentityStateError",
    "IdentityStore",
    "LOCK_FILENAME",
    "STATE_FILENAME",
    "STATE_SCHEMA_VERSION",
    "build_default_state_dir",
    "derive_replica_instance_id",
    "peek_instance_id",
]
