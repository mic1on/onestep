from datetime import UTC, datetime, timedelta

from onestep_control_plane_api.main import app
from onestep_control_plane_api.ops.readiness import build_default_background_task_states


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
        payload["checks"]["background_tasks"]["notification_missed_start_scanner"]["ready"]
        is True
    )


def test_readyz_returns_503_when_background_task_is_missing(client) -> None:
    app.state.background_task_states = build_default_background_task_states()
    app.state.background_task_refs = {
        "notification_missed_start_scanner": None,
    }

    response = client.get("/readyz")

    assert response.status_code == 503
    payload = response.json()
    assert payload["status"] == "not_ready"
    assert (
        payload["checks"]["background_tasks"]["notification_missed_start_scanner"]["detail"]
        == "background task is not registered"
    )


def test_readyz_returns_503_when_background_task_is_stalled(client) -> None:
    app.state.background_task_states = build_default_background_task_states()
    state = app.state.background_task_states["notification_missed_start_scanner"]
    stale_time = datetime.now(UTC) - timedelta(seconds=600)
    state.mark_started(stale_time)
    state.mark_success(stale_time)
    app.state.background_task_refs = {
        "notification_missed_start_scanner": object(),
    }

    response = client.get("/readyz")

    assert response.status_code == 503
    payload = response.json()
    assert payload["status"] == "not_ready"
    assert (
        payload["checks"]["background_tasks"]["notification_missed_start_scanner"]["detail"]
        == "background task is stalled"
    )
