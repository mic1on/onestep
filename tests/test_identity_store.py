from __future__ import annotations

import json
import os

import pytest

from onestep.identity_store import IdentityLockError, IdentityStore


def test_identity_store_persists_instance_id_and_sequences(tmp_path) -> None:
    store = IdentityStore(tmp_path)
    instance_id = store.load_or_create_instance_id()

    assert store.next_heartbeat_sequence() == 1
    assert store.next_heartbeat_sequence() == 2
    assert store.next_sync_sequence() == 1
    assert store.next_sync_sequence() == 2

    store.close()

    state_payload = json.loads((tmp_path / "identity.json").read_text())
    assert state_payload["schema_version"] == 1
    assert state_payload["instance_id"] == str(instance_id)
    assert state_payload["heartbeat_sequence"] == 2
    assert state_payload["sync_sequence"] == 2

    reopened = IdentityStore(tmp_path)
    assert reopened.load_or_create_instance_id() == instance_id
    assert reopened.next_heartbeat_sequence() == 3
    assert reopened.next_sync_sequence() == 3
    reopened.close()


def test_identity_store_rejects_second_owner_for_same_state_dir(tmp_path) -> None:
    store = IdentityStore(tmp_path)

    with pytest.raises(IdentityLockError, match=str(tmp_path)):
        IdentityStore(tmp_path)

    store.close()


def test_identity_store_reclaims_stale_lock_file(tmp_path) -> None:
    tmp_path.mkdir(exist_ok=True)
    lock_path = tmp_path / "identity.lock"
    lock_path.write_text(
        json.dumps(
            {
                "pid": 999_999,
                "created_at": "2026-03-19T08:30:00Z",
            }
        )
    )

    store = IdentityStore(tmp_path)

    lock_payload = json.loads(lock_path.read_text())
    assert lock_payload["pid"] == os.getpid()

    store.close()
    assert lock_path.exists() is False
