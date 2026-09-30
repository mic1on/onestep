"""The presence beacon endpoint (``POST /api/v1/agents/presence``).

What this endpoint promises, and what these tests pin:

* it advances ``instances.last_seen_at`` -- the only column it may write;
* it never touches ``last_heartbeat_sequence`` / ``last_heartbeat_sent_at`` /
  ``status`` / ``app_snapshot_json``, so a beacon can neither reorder the
  heartbeat stream nor overwrite the last health/task-control snapshot;
* the consequence that matters: an instance whose event loop is blocked inside a
  synchronous handler stays ``online`` and produces **no** ``instance_offline``
  notification, which is the whole reason the endpoint exists.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi import HTTPException
from onestep_control_plane_api.api.agent_ingestion_service import (
    ingest_heartbeat_request,
    ingest_presence_request,
)
from onestep_control_plane_api.api.notification_service import (
    scan_and_dispatch_instance_connectivity_notifications,
)
from onestep_control_plane_api.api.query_support import get_instance_connectivity, online_cutoff
from onestep_control_plane_api.api.schemas import (
    AgentPresenceRequest,
    HeartbeatIngestRequest,
)
from onestep_control_plane_api.core.settings import settings
from onestep_control_plane_api.db.models import (
    Instance,
    NotificationDelivery,
    Service,
)
from onestep_control_plane_api.db.session import session_scope
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_agent_ingestion import ingest_heartbeat, make_heartbeat_payload
from test_notification_service import seed_channel

INSTANCE_ID = "8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df"
OTHER_INSTANCE_ID = "4f25903d-44d7-4f5a-a7aa-865c2665289a"

OFFLINE_AFTER = settings.instance_offline_after_s
CONFIRM_AFTER = settings.instance_connectivity_confirm_after_s

BASE = datetime(2026, 4, 30, 2, 0, 0, tzinfo=UTC)


def at(seconds: float) -> datetime:
    return BASE + timedelta(seconds=seconds)


def presence_payload(
    instance_id: str = INSTANCE_ID,
    *,
    name: str = "billing-sync",
    environment: str = "prod",
    sent_at: str = "2026-03-08T17:31:00Z",
) -> dict[str, object]:
    return {
        "service": {
            "name": name,
            "environment": environment,
            "node_name": "vm-prod-3",
            "instance_id": instance_id,
            "deployment_version": "1.0.0a0+c435c99",
        },
        "sent_at": sent_at,
    }


async def _run_work_unit(work_unit, *args, **kwargs):
    async with session_scope() as session:
        return await work_unit(session, *args, **kwargs)


def ingest_presence(payload: dict[str, object], *, received_at: datetime):
    """Drive the async presence ingest from a synchronous test."""

    return asyncio.run(
        _run_work_unit(
            ingest_presence_request,
            AgentPresenceRequest.model_validate(payload),
            received_at=received_at,
        )
    )


def ingest_heartbeat_at(payload: dict[str, object], *, received_at: datetime):
    return asyncio.run(
        _run_work_unit(
            ingest_heartbeat_request,
            HeartbeatIngestRequest.model_validate(payload),
            received_at=received_at,
        )
    )


def load_instance(db_session: Session, instance_id: str = INSTANCE_ID) -> Instance:
    # The sync fixture holds its own identity map, and the writes under test
    # happen on the async engine's session, so a stale attribute would silently
    # make every assertion about "did this column change" pass.
    db_session.expire_all()
    instance = db_session.scalar(select(Instance).where(Instance.instance_id == UUID(instance_id)))
    assert instance is not None
    return instance


def deliveries(db_session: Session) -> list[NotificationDelivery]:
    return list(
        db_session.scalars(
            select(NotificationDelivery).order_by(NotificationDelivery.created_at)
        ).all()
    )


# ---------------------------------------------------------------------------
# Service layer
# ---------------------------------------------------------------------------


def test_presence_creates_service_and_instance_and_marks_it_online(db_session, async_db) -> None:
    """A beacon that arrives before the first sync must not be dropped.

    It creates the rows it needs, and the instance looks exactly like one that
    has never sent telemetry: ``status="unknown"``, no runtime descriptor, no
    heartbeat sequence.
    """

    response = ingest_presence(presence_payload(), received_at=at(10))

    assert response.status == "accepted"
    assert response.received_at == at(10)

    service = db_session.scalar(
        select(Service).where(Service.name == "billing-sync", Service.environment == "prod")
    )
    assert service is not None
    instance = load_instance(db_session)
    assert instance.service_id == service.id
    assert instance.last_seen_at == at(10)
    assert instance.status == "unknown"
    assert instance.last_heartbeat_sequence is None
    assert instance.app_snapshot_json is None
    assert get_instance_connectivity(instance, cutoff=at(10)) == "online"


def test_presence_advances_last_seen_at_monotonically(db_session, async_db) -> None:
    """A late or clock-skewed beacon must never manufacture an outage."""

    ingest_presence(presence_payload(), received_at=at(100))
    assert load_instance(db_session).last_seen_at == at(100)

    # Out of order: older than what is already recorded -> ignored.
    ingest_presence(presence_payload(), received_at=at(40))
    assert load_instance(db_session).last_seen_at == at(100)

    # Newer -> advanced.
    ingest_presence(presence_payload(), received_at=at(160))
    assert load_instance(db_session).last_seen_at == at(160)


def test_presence_leaves_heartbeat_state_untouched(db_session, async_db) -> None:
    """The beacon is a liveness probe, not telemetry.

    This is the property that makes it safe to run concurrently with the
    heartbeat: it carries no sequence, so there is nothing to compare and
    nothing to overwrite.
    """

    ingest_heartbeat(
        db_session,
        make_heartbeat_payload(
            sequence=7,
            status="degraded",
            task_controls=[
                {
                    "task_name": "process_orders",
                    "pause_requested": True,
                    "paused": True,
                    "accepting_new_work": False,
                    "runner_count": 2,
                    "parked_runner_count": 2,
                    "fetching_runner_count": 0,
                    "inflight_task_count": 0,
                }
            ],
        ),
    )
    before = load_instance(db_session)
    before_sequence = before.last_heartbeat_sequence
    before_sent_at = before.last_heartbeat_sent_at
    before_status = before.status
    before_snapshot = before.app_snapshot_json
    before_seen = before.last_seen_at
    assert before_sequence == 7
    assert before_status == "degraded"
    assert before_snapshot is not None

    ingest_presence(presence_payload(), received_at=before_seen + timedelta(seconds=30))

    after = load_instance(db_session)
    assert after.last_seen_at == before_seen + timedelta(seconds=30)
    assert after.last_heartbeat_sequence == before_sequence
    assert after.last_heartbeat_sent_at == before_sent_at
    assert after.status == before_status
    assert after.app_snapshot_json == before_snapshot


def test_presence_rejects_an_identity_that_does_not_match_the_instance(
    db_session, async_db
) -> None:
    ingest_heartbeat(
        db_session,
        make_heartbeat_payload(instance_id=OTHER_INSTANCE_ID),
    )

    with pytest.raises(HTTPException) as excinfo:
        ingest_presence(
            presence_payload(OTHER_INSTANCE_ID, name="other-sync"),
            received_at=at(200),
        )

    assert excinfo.value.status_code == 409


def _seed_online_and_channel(db_session, monkeypatch) -> None:
    """Put the instance on the books as online, with a subscribed channel.

    The first scan of an instance only seeds the connectivity state (there is no
    prior state to have flipped from), so a test about flips has to let that
    seeding scan happen while the instance is genuinely online.
    """

    monkeypatch.setattr(
        "onestep_control_plane_api.api.notification_service._post_webhook",
        lambda delivery, *, webhook_url, timeout_s=5.0: None,
    )
    seed_channel(db_session, event_types=["instance_online", "instance_offline"])
    ingest_heartbeat_at(make_heartbeat_payload(sequence=1), received_at=at(0))
    assert load_instance(db_session).last_seen_at == at(0)
    assert scan_and_dispatch_instance_connectivity_notifications(db_session, now=at(0)) == 0


def test_presence_keeps_a_stalled_instance_online(db_session, async_db, monkeypatch) -> None:
    """The end-to-end reason this endpoint exists.

    Timeline, with the shipped defaults (heartbeat 30 s, so beacon grace 60 s and
    interval 30 s; offline window 90 s, confirmation 30 s):

    * t=0 the instance checks in, then its event loop blocks inside a synchronous
      handler, so no heartbeat can be sent;
    * t=60 the beacon's grace window expires and it reports liveness; every 30 s
      after that it reports again;
    * the offline observation never exists, because ``last_seen_at`` is never
      allowed to go stale -- so there is no pending flip to confirm at all;
    * nothing is delivered. Without the beacon this is an ``instance_offline`` at
      t=120 followed by an ``instance_online`` when the handler returns.
    """

    _seed_online_and_channel(db_session, monkeypatch)

    # The beacon fires every 30 s while the loop is blocked, from t=60 onward.
    for offset in (60, 90, 120, 150):
        ingest_presence(presence_payload(), received_at=at(offset))

    # Scans land between and after the beacons, always with a fresh last_seen.
    for offset in (61, 91, 121, 151, 180):
        assert (
            scan_and_dispatch_instance_connectivity_notifications(db_session, now=at(offset)) == 0
        )
        instance = load_instance(db_session)
        assert get_instance_connectivity(instance, cutoff=online_cutoff(at(offset))) == "online"

    assert deliveries(db_session) == []


def test_without_presence_the_same_timeline_flaps(db_session, async_db, monkeypatch) -> None:
    """Negative control: the beacon is what makes the difference.

    Identical timeline to :func:`test_presence_keeps_a_stalled_instance_online`
    with the beacons removed. The instance is reported offline and then online
    again, which is precisely the flap the endpoint exists to prevent. Without
    this control the positive test would also pass if the scan simply never
    alerted.
    """

    _seed_online_and_channel(db_session, monkeypatch)

    delivered = 0
    for offset in (61, 91, 121, 151):
        delivered += scan_and_dispatch_instance_connectivity_notifications(
            db_session, now=at(offset)
        )

    # The loop recovers at t=180: the handler returned and a heartbeat lands.
    ingest_heartbeat_at(make_heartbeat_payload(sequence=2), received_at=at(180))
    delivered += scan_and_dispatch_instance_connectivity_notifications(db_session, now=at(180))

    assert [delivery.event_type for delivery in deliveries(db_session)] == [
        "instance_offline",
        "instance_online",
    ]
    assert delivered == 2


def test_presence_cancels_an_already_pending_offline_flip(
    db_session, async_db, monkeypatch
) -> None:
    """A beacon that arrives after the observation flipped still cancels it.

    This is the worst-case timing: the loop stalled long enough that the scan
    already saw ``offline`` and parked the flip, but not long enough for the
    confirmation window to elapse. The beacon then proves the instance is alive
    and the parked flip heals, exactly like a heartbeat would.
    """

    _seed_online_and_channel(db_session, monkeypatch)

    # Past the offline window: the observation flips, but has not held 30 s.
    assert (
        scan_and_dispatch_instance_connectivity_notifications(
            db_session, now=at(OFFLINE_AFTER + 10)
        )
        == 0
    )

    # The beacon reports liveness from a thread that is not the blocked loop.
    ingest_presence(presence_payload(), received_at=at(OFFLINE_AFTER + 10))
    assert load_instance(db_session).last_seen_at == at(OFFLINE_AFTER + 10)

    assert (
        scan_and_dispatch_instance_connectivity_notifications(
            db_session, now=at(OFFLINE_AFTER + 10 + CONFIRM_AFTER)
        )
        == 0
    )
    assert deliveries(db_session) == []
    # Still online immediately after the beacon; it would only fall offline once
    # the beacon itself went quiet for a whole offline window.
    assert (
        get_instance_connectivity(load_instance(db_session), cutoff=online_cutoff(at(115)))
        == "online"
    )


# ---------------------------------------------------------------------------
# HTTP endpoint
# ---------------------------------------------------------------------------


def test_presence_endpoint_accepts_and_advances_last_seen_at(
    client, db_session, auth_headers
) -> None:
    response = client.post(
        "/api/v1/agents/presence",
        json=presence_payload(),
        headers=auth_headers,
    )

    assert response.status_code == 202
    assert response.json()["status"] == "accepted"
    instance = load_instance(db_session)
    assert instance.last_seen_at is not None
    assert instance.status == "unknown"
    assert instance.last_heartbeat_sequence is None
    assert get_instance_connectivity(instance, cutoff=online_cutoff(datetime.now(UTC))) == "online"


def test_presence_endpoint_requires_a_bearer_token(client) -> None:
    response = client.post("/api/v1/agents/presence", json=presence_payload())

    assert response.status_code == 401


def test_presence_endpoint_rejects_a_bad_token(client) -> None:
    response = client.post(
        "/api/v1/agents/presence",
        json=presence_payload(),
        headers={"Authorization": "Bearer not-the-ingest-token"},
    )

    assert response.status_code == 401


def test_presence_endpoint_rejects_a_body_with_a_sequence(client, auth_headers) -> None:
    """The frame is deliberately not an ``IngestionEnvelope``.

    A sequence here would imply ordering against the heartbeat stream, which
    this endpoint must not participate in. Rejecting the field keeps the
    contract honest instead of silently ignoring it.
    """

    payload = {**presence_payload(), "sequence": 3}
    response = client.post("/api/v1/agents/presence", json=payload, headers=auth_headers)

    assert response.status_code == 422


def test_presence_endpoint_returns_409_on_identity_mismatch(
    client, db_session, auth_headers
) -> None:
    ingest_heartbeat(db_session, make_heartbeat_payload(instance_id=OTHER_INSTANCE_ID))

    response = client.post(
        "/api/v1/agents/presence",
        json=presence_payload(OTHER_INSTANCE_ID, name="other-sync"),
        headers=auth_headers,
    )

    assert response.status_code == 409
