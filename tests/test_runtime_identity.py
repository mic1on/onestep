from __future__ import annotations

import asyncio
from uuid import UUID

from onestep import ControlPlaneReporter, ControlPlaneWsSender, OneStepApp
from onestep.identity_store import IdentityStore, derive_replica_instance_id

from control_plane_testkit import RecordingTransport, make_config


def test_reporter_startup_reuses_persisted_instance_id_and_refreshes_session_id(
    tmp_path,
) -> None:
    async def run_once(session_id: str) -> tuple[UUID, object, str | None]:
        app = OneStepApp("billing-sync")
        transport = RecordingTransport(session_ids=[session_id])
        config = make_config(instance_id=None, state_dir=str(tmp_path))
        sender = ControlPlaneWsSender(config, transport=transport)
        reporter = ControlPlaneReporter(config, sender=sender)
        reporter.attach(app)
        await app.startup()
        for _ in range(50):
            if transport.connect_calls and reporter.session_id is not None:
                break
            await asyncio.sleep(0.01)
        connected_instance_id = transport.connect_calls[0][0]["instance_id"]
        started_at = transport.connect_calls[0][1]["started_at"]
        negotiated_session_id = reporter.session_id
        await app.shutdown()
        return connected_instance_id, started_at, negotiated_session_id

    first_instance_id, first_started_at, first_session_id = asyncio.run(run_once("sess_first"))
    second_instance_id, second_started_at, second_session_id = asyncio.run(run_once("sess_second"))

    assert first_instance_id == second_instance_id
    assert first_started_at != second_started_at
    assert first_session_id == "sess_first"
    assert second_session_id == "sess_second"


def test_reporter_config_from_env_precedence_prefers_instance_id_then_replica_key_then_state_dir(
    monkeypatch,
    tmp_path,
) -> None:
    state_dir = tmp_path / "identity"
    persisted_instance_id = UUID("f13b655a-b4c9-4b69-a8d4-3027f3fa7415")
    store = IdentityStore(state_dir, instance_id=persisted_instance_id)
    store.close()

    monkeypatch.setenv("ONESTEP_CONTROL_PLANE_URL", "https://control-plane.example.com")
    monkeypatch.setenv("ONESTEP_CONTROL_PLANE_TOKEN", "secret-token")
    monkeypatch.setenv("ONESTEP_SERVICE_NAME", "billing-sync")
    monkeypatch.setenv("ONESTEP_ENV", "prod")
    monkeypatch.setenv("ONESTEP_STATE_DIR", str(state_dir))
    monkeypatch.setenv("ONESTEP_REPLICA_KEY", "worker-7")
    monkeypatch.setenv("ONESTEP_INSTANCE_ID", "4f25903d-44d7-4f5a-a7aa-865c2665289a")

    override_config = make_config().from_env(app_name="ignored-app")
    assert override_config.instance_id == UUID("4f25903d-44d7-4f5a-a7aa-865c2665289a")

    monkeypatch.delenv("ONESTEP_INSTANCE_ID")
    replica_config = make_config().from_env(app_name="ignored-app")
    assert replica_config.instance_id == derive_replica_instance_id(
        service_name="billing-sync",
        environment="prod",
        replica_key="worker-7",
    )

    monkeypatch.delenv("ONESTEP_REPLICA_KEY")
    state_config = make_config().from_env(app_name="ignored-app")
    assert state_config.instance_id == persisted_instance_id
