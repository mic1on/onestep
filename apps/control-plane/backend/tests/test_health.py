from datetime import UTC, datetime, timedelta

from onestep_control_plane_api.main import app
from onestep_control_plane_api.ops.readiness import build_default_background_task_states
from onestep_control_plane_api.workers.notification_scanner import (
    NOTIFICATION_MISSED_START_SCANNER_NAME,
)
from onestep_control_plane_api.workers.retention_worker import RETENTION_WORKER_NAME


def _mark_background_tasks_ready() -> None:
    app.state.background_task_states = build_default_background_task_states()
    now = datetime.now(UTC)
    for state in app.state.background_task_states.values():
        state.mark_started(now)
        state.mark_leader("local", when=now)
        state.mark_success(now)


def test_healthz(client) -> None:
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "ingestion_auth_configured": True,
    }


def test_readyz_returns_ready_when_all_checks_pass(client) -> None:
    response = client.get("/readyz")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["ingestion_auth_configured"] is True
    assert payload["checks"]["database"]["ready"] is True
    assert payload["checks"]["migrations"]["ready"] is True
    assert (
        payload["checks"]["background_tasks"][NOTIFICATION_MISSED_START_SCANNER_NAME]["ready"]
        is True
    )
    assert (
        payload["checks"]["background_tasks"][RETENTION_WORKER_NAME]["ready"] is True
    )


def test_readyz_returns_503_when_background_task_is_missing(client) -> None:
    _mark_background_tasks_ready()
    app.state.background_task_refs = {
        NOTIFICATION_MISSED_START_SCANNER_NAME: object(),
        RETENTION_WORKER_NAME: None,
    }

    response = client.get("/readyz")

    assert response.status_code == 503
    payload = response.json()
    assert payload["status"] == "not_ready"
    assert (
        payload["checks"]["background_tasks"][RETENTION_WORKER_NAME]["detail"]
        == "background task is not registered"
    )


def test_readyz_returns_503_when_background_task_is_stalled(client) -> None:
    _mark_background_tasks_ready()
    state = app.state.background_task_states[RETENTION_WORKER_NAME]
    stale_time = datetime.now(UTC) - timedelta(seconds=600)
    state.mark_started(stale_time)
    state.mark_success(stale_time)
    app.state.background_task_refs = {
        NOTIFICATION_MISSED_START_SCANNER_NAME: object(),
        RETENTION_WORKER_NAME: object(),
    }

    response = client.get("/readyz")

    assert response.status_code == 503
    payload = response.json()
    assert payload["status"] == "not_ready"
    assert (
        payload["checks"]["background_tasks"][RETENTION_WORKER_NAME]["detail"]
        == "background task is stalled"
    )
