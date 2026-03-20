from __future__ import annotations

import json
import os
import socket
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


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


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
        char if char.isalnum() or char in {"-", "_", "."} else "_"
        for char in normalized
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
        self._lock_owner: dict[str, Any] | None = None
        self._closed = False
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._acquire_lock()
        self._state = self._load_state(instance_id=instance_id)

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
        try:
            self.lock_path.unlink()
        except FileNotFoundError:
            return

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
        for _ in range(2):
            try:
                fd = os.open(
                    self.lock_path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
            except FileExistsError:
                if self._reclaim_stale_lock():
                    continue
                owner = _read_lock_owner(self.lock_path)
                owner_pid = owner.get("pid")
                raise IdentityLockError(
                    "identity state dir "
                    f"{self.state_dir} is already locked"
                    + (f" by pid={owner_pid}" if isinstance(owner_pid, int) else "")
                )
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(lock_payload, handle, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            self._lock_owner = lock_payload
            return
        raise IdentityLockError(f"failed to acquire identity lock for {self.state_dir}")

    def _reclaim_stale_lock(self) -> bool:
        owner = _read_lock_owner(self.lock_path)
        owner_pid = owner.get("pid")
        if not isinstance(owner_pid, int):
            return False
        if _pid_is_running(owner_pid):
            return False
        try:
            self.lock_path.unlink()
        except FileNotFoundError:
            return True
        return True


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


def _read_lock_owner(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


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
