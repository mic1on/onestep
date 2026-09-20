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

    store.close()
    lock_payload = json.loads(lock_path.read_text())
    assert lock_payload["pid"] == os.getpid()

    assert lock_path.exists() is True


@pytest.mark.parametrize("contents", [json.dumps({"pid": os.getpid()}), "", "{broken"])
def test_unlocked_metadata_does_not_block_startup(tmp_path, contents):
    (tmp_path / "identity.lock").write_text(contents)
    with IdentityStore(tmp_path):
        pass


def test_failed_state_load_releases_lock(tmp_path):
    from onestep.identity_store import IdentityStateError

    (tmp_path / "identity.json").write_text("{broken")
    with pytest.raises(IdentityStateError):
        IdentityStore(tmp_path)
    (tmp_path / "identity.json").unlink()
    with IdentityStore(tmp_path):
        pass


def test_close_preserves_lock_inode_and_is_idempotent(tmp_path):
    store = IdentityStore(tmp_path)
    inode = (tmp_path / "identity.lock").stat().st_ino
    store.close()
    with IdentityStore(tmp_path):
        store.close()
        assert (tmp_path / "identity.lock").stat().st_ino == inode
        with pytest.raises(IdentityLockError):
            IdentityStore(tmp_path)


@pytest.mark.parametrize("termination", ["normal", "terminate", "kill"])
def test_process_exit_releases_lock_and_preserves_state(tmp_path, termination):
    import subprocess
    import sys

    code = """
import sys
from onestep.identity_store import IdentityStore
with IdentityStore(sys.argv[1]) as store:
    store.next_heartbeat_sequence()
    print(store.instance_id, flush=True)
    sys.stdin.readline()
"""
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(tmp_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        # communicate has a timeout below; readiness is bounded with a reader thread.
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(1) as pool:
            ready = pool.submit(process.stdout.readline)
            try:
                instance_id = ready.result(timeout=10).strip()
            except BaseException:
                process.kill()
                raise
        assert instance_id
        with pytest.raises(IdentityLockError):
            IdentityStore(tmp_path)
        if termination == "kill":
            process.kill()
        elif termination == "terminate":
            process.terminate()
        process.communicate(input="\n", timeout=10)
        with IdentityStore(tmp_path) as reopened:
            assert str(reopened.instance_id) == instance_id
            assert reopened.next_heartbeat_sequence() == 2
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)


def test_metadata_write_failure_releases_lock(tmp_path, monkeypatch):
    def fail(_fd):
        raise OSError("disk failure")

    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", fail)
        with pytest.raises(OSError, match="disk failure"):
            IdentityStore(tmp_path)
    with IdentityStore(tmp_path):
        pass


def test_contender_does_not_modify_owner_metadata(tmp_path):
    with IdentityStore(tmp_path):
        before = (tmp_path / "identity.lock").stat()
        with pytest.raises(IdentityLockError):
            IdentityStore(tmp_path)
        after = (tmp_path / "identity.lock").stat()
        assert (after.st_size, after.st_mtime_ns) == (before.st_size, before.st_mtime_ns)
    assert json.loads((tmp_path / "identity.lock").read_text())["pid"] == os.getpid()
