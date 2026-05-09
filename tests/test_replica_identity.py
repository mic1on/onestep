from __future__ import annotations

import asyncio

import pytest

from onestep import ControlPlaneReporter, OneStepApp
from onestep.identity_store import IdentityLockError, derive_replica_instance_id

from control_plane_testkit import SenderRecorder, make_config


def test_replica_keys_derive_distinct_instance_ids() -> None:
    worker_zero = derive_replica_instance_id(
        service_name="billing-sync",
        environment="prod",
        replica_key="worker-0",
    )
    worker_one = derive_replica_instance_id(
        service_name="billing-sync",
        environment="prod",
        replica_key="worker-1",
    )

    assert worker_zero != worker_one


def test_reporter_rejects_second_worker_sharing_same_state_dir(tmp_path) -> None:
    recorder_one = SenderRecorder()
    recorder_two = SenderRecorder()
    app_one = OneStepApp("billing-sync")
    app_two = OneStepApp("billing-sync")
    reporter_one = ControlPlaneReporter(
        make_config(instance_id=None, state_dir=str(tmp_path)),
        sender=recorder_one,
    )
    reporter_two = ControlPlaneReporter(
        make_config(instance_id=None, state_dir=str(tmp_path)),
        sender=recorder_two,
    )
    reporter_one.attach(app_one)
    reporter_two.attach(app_two)

    async def scenario() -> None:
        await app_one.startup()
        try:
            with pytest.raises(IdentityLockError, match=str(tmp_path)):
                await app_two.startup()
        finally:
            await app_one.shutdown()

    asyncio.run(scenario())
