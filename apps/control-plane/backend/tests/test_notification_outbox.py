from __future__ import annotations

import asyncio
import threading
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI
from onestep_control_plane_api.api.agent_ingestion_service import ingest_events_request
from onestep_control_plane_api.api.notification_service import (
    claim_pending_outbox_rows,
    delete_notification_channel,
    dispatch_runtime_task_event_notifications,
    drain_notification_outbox,
)
from onestep_control_plane_api.api.schemas import (
    EventsIngestRequest,
    ServiceDescriptor,
    TaskEventRecord,
    TaskFailureDescriptor,
)
from onestep_control_plane_api.db.base import Base
from onestep_control_plane_api.db.models import (
    Instance,
    NotificationChannel,
    NotificationDelivery,
    NotificationOutbox,
    Service,
    TaskEvent,
)
from onestep_control_plane_api.ops.readiness import build_default_background_task_states
from onestep_control_plane_api.workers.leader import WorkerLease
from onestep_control_plane_api.workers.notification_outbox_worker import (
    NOTIFICATION_OUTBOX_WORKER_NAME,
    run_notification_outbox_worker,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool


def _utcnow() -> datetime:
    return datetime.now(UTC)


def seed_service_and_instance(db: Session) -> tuple[Service, Instance]:
    service = Service(
        name="billing-sync",
        environment="prod",
        latest_deployment_version="1.0.0",
        latest_sync_at=datetime(2026, 4, 30, 2, 0, 0, tzinfo=UTC),
    )
    db.add(service)
    db.commit()
    instance = Instance(
        service_id=service.id,
        instance_id=uuid4(),
        node_name="vm-1",
        deployment_version="1.0.0",
        status="ok",
        created_at=datetime(2026, 4, 30, 2, 0, 0, tzinfo=UTC),
        updated_at=datetime(2026, 4, 30, 2, 0, 0, tzinfo=UTC),
    )
    db.add(instance)
    db.commit()
    db.refresh(service)
    db.refresh(instance)
    return service, instance


def seed_channel(db: Session, *, event_types: list[str]) -> NotificationChannel:
    channel = NotificationChannel(
        name=f"ops-{uuid4().hex[:6]}",
        provider="feishu",
        webhook_url="https://example.com/hook",
        enabled=True,
        service_scopes_json=[{"name": "billing-sync", "environment": "prod"}],
        event_types_json=event_types,
        missed_start_grace_seconds=300,
    )
    db.add(channel)
    db.commit()
    db.refresh(channel)
    return channel


def seed_failed_task_event(db: Session, service: Service, instance: Instance) -> TaskEvent:
    task_event = TaskEvent(
        event_id=f"evt-{uuid4().hex[:8]}",
        service_id=service.id,
        instance_id=instance.instance_id,
        task_name="sync_users",
        kind="failed",
        occurred_at=datetime(2026, 4, 30, 2, 5, 0, tzinfo=UTC),
        attempts=1,
        duration_ms=1234,
        failure_kind="timeout",
        exception_type="TimeoutError",
        message="upstream timeout",
        meta_json={"scheduled_at": "2026-04-30T02:00:00Z"},
        received_at=datetime(2026, 4, 30, 2, 5, 1, tzinfo=UTC),
    )
    db.add(task_event)
    db.commit()
    db.refresh(task_event)
    return task_event


def test_receive_path_does_not_block_on_slow_webhook(db_session, monkeypatch) -> None:
    """A slow/hung webhook must not stall the telemetry receive path.

    The receive path (``dispatch_runtime_task_event_notifications``, called from
    the agent WS ingest loop) only performs a fast outbox insert and returns
    immediately; the blocking HTTP POST happens later, off the event loop.
    """
    service, instance = seed_service_and_instance(db_session)
    seed_channel(db_session, event_types=["task_failed"])
    task_event = seed_failed_task_event(db_session, service, instance)

    slow_delay_s = 1.5

    def slow_post_webhook(delivery, *, webhook_url: str, timeout_s: float = 5.0) -> None:
        # Simulate a hung/slow downstream webhook.
        time.sleep(slow_delay_s)
        delivery.status = "succeeded"
        delivery.response_status_code = 200
        delivery.sent_at = _utcnow()

    monkeypatch.setattr(
        "onestep_control_plane_api.api.notification_service._post_webhook",
        slow_post_webhook,
    )

    start = time.monotonic()
    created_count = dispatch_runtime_task_event_notifications(
        db_session, task_events=[task_event]
    )
    elapsed_s = time.monotonic() - start

    assert created_count == 1
    # The receive path returned in a small fraction of the webhook latency:
    # it never waited for the HTTP call.
    assert elapsed_s < slow_delay_s, (
        f"receive path took {elapsed_s:.3f}s, expected to be well under "
        f"{slow_delay_s}s (no inline HTTP)"
    )

    outbox = db_session.query(NotificationOutbox).one()
    assert outbox.status == "pending"
    assert outbox.attempts == 0
    delivery = db_session.query(NotificationDelivery).one()
    assert delivery.status == "pending"
    assert delivery.sent_at is None


def test_ingest_events_receive_path_does_not_block_on_slow_webhook(
    db_session, monkeypatch
) -> None:
    """The full agent WS ingest receive path stays fast under a slow webhook."""
    seed_service_and_instance(db_session)
    seed_channel(db_session, event_types=["task_failed"])

    slow_delay_s = 1.0

    def slow_post_webhook(delivery, *, webhook_url: str, timeout_s: float = 5.0) -> None:
        time.sleep(slow_delay_s)
        delivery.status = "succeeded"
        delivery.response_status_code = 200
        delivery.sent_at = _utcnow()

    monkeypatch.setattr(
        "onestep_control_plane_api.api.notification_service._post_webhook",
        slow_post_webhook,
    )

    request = EventsIngestRequest(
        service=ServiceDescriptor(
            name="billing-sync",
            environment="prod",
            node_name="vm-1",
            instance_id=uuid4(),
            deployment_version="1.0.0",
        ),
        sent_at=datetime(2026, 4, 30, 2, 5, 0, tzinfo=UTC),
        sequence=1,
        events=[
            TaskEventRecord(
                event_id=f"evt-{uuid4().hex[:8]}",
                kind="failed",
                task_name="sync_users",
                occurred_at=datetime(2026, 4, 30, 2, 5, 0, tzinfo=UTC),
                attempts=1,
                duration_ms=1234,
                failure=TaskFailureDescriptor(
                    kind="timeout",
                    exception_type="TimeoutError",
                    message="upstream timeout",
                ),
                meta={"scheduled_at": "2026-04-30T02:00:00Z"},
            )
        ],
    )

    start = time.monotonic()
    response = ingest_events_request(db_session, request)
    elapsed_s = time.monotonic() - start

    assert response.ingested_count == 1
    assert elapsed_s < slow_delay_s
    assert db_session.query(NotificationOutbox).count() == 1


def test_outbox_snapshots_webhook_url_at_enqueue(db_session, monkeypatch) -> None:
    """The webhook target is frozen at enqueue so a later channel edit cannot
    reroute a delivery already queued for dispatch."""
    service, instance = seed_service_and_instance(db_session)
    channel = seed_channel(db_session, event_types=["task_failed"])
    original_url = channel.webhook_url
    task_event = seed_failed_task_event(db_session, service, instance)

    dispatch_runtime_task_event_notifications(db_session, task_events=[task_event])

    outbox = db_session.query(NotificationOutbox).one()
    assert outbox.webhook_url == original_url

    # Edit the channel after enqueue.
    channel.webhook_url = "https://rotated.example.com/hook"
    db_session.commit()
    db_session.refresh(outbox)
    assert outbox.webhook_url == original_url


def test_outbox_uses_frozen_provider_contract_after_channel_edit(
    db_session, monkeypatch
) -> None:
    service, instance = seed_service_and_instance(db_session)
    channel = NotificationChannel(
        name=f"ops-custom-{uuid4().hex[:6]}",
        provider="custom",
        webhook_url="https://example.com/custom",
        enabled=True,
        service_scopes_json=[{"name": "billing-sync", "environment": "prod"}],
        event_types_json=["task_failed"],
        missed_start_grace_seconds=300,
        custom_config_json={
            "method": "GET",
            "query_params": [{"key": "service", "value": "{{ service_name }}"}],
            "body_params": [],
        },
    )
    db_session.add(channel)
    db_session.commit()
    task_event = seed_failed_task_event(db_session, service, instance)

    dispatch_runtime_task_event_notifications(db_session, task_events=[task_event])

    outbox = db_session.query(NotificationOutbox).one()
    assert outbox.provider == "custom"
    assert outbox.webhook_method == "GET"
    channel.provider = "feishu"
    channel.custom_config_json = None
    db_session.commit()

    sent_requests: list[tuple[str, str, dict[str, object]]] = []

    class FakeResponse:
        status_code = 200
        text = "ok"
        is_success = True

    class FakeClient:
        def __init__(self, *, timeout: float) -> None:
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback) -> None:
            return None

        def get(self, url: str, *, params: dict[str, object]):
            sent_requests.append(("GET", url, params))
            return FakeResponse()

        def post(
            self,
            url: str,
            *,
            params: dict[str, object] | None = None,
            json: dict[str, object] | None = None,
        ):
            sent_requests.append(("POST", url, params or json or {}))
            return FakeResponse()

    monkeypatch.setattr(
        "onestep_control_plane_api.api.notification_service.httpx.Client",
        FakeClient,
    )

    assert drain_notification_outbox(db_session, now=_utcnow()) == 1
    assert sent_requests == [
        ("GET", "https://example.com/custom", {"service": "billing-sync"})
    ]


def test_channel_delete_preserves_and_drains_accepted_delivery(
    db_session, monkeypatch
) -> None:
    service, instance = seed_service_and_instance(db_session)
    channel = seed_channel(db_session, event_types=["task_failed"])
    task_event = seed_failed_task_event(db_session, service, instance)
    dispatch_runtime_task_event_notifications(db_session, task_events=[task_event])

    delete_notification_channel(db_session, channel.id)

    delivery = db_session.query(NotificationDelivery).one()
    assert delivery.channel_id is None
    assert db_session.query(NotificationOutbox).count() == 1

    def ok_post_webhook(delivery, *, webhook_url: str, timeout_s: float = 5.0) -> None:
        delivery.status = "succeeded"
        delivery.response_status_code = 200
        delivery.sent_at = _utcnow()

    monkeypatch.setattr(
        "onestep_control_plane_api.api.notification_service._post_webhook",
        ok_post_webhook,
    )

    assert drain_notification_outbox(db_session, now=_utcnow()) == 1
    assert db_session.query(NotificationOutbox).one().status == "delivered"


def test_channel_delete_removes_terminal_history_but_preserves_retries(db_session) -> None:
    service, instance = seed_service_and_instance(db_session)
    channel = seed_channel(db_session, event_types=["task_failed"])
    deliveries: list[NotificationDelivery] = []
    for _ in range(4):
        task_event = seed_failed_task_event(db_session, service, instance)
        dispatch_runtime_task_event_notifications(db_session, task_events=[task_event])
        deliveries.append(
            db_session.query(NotificationDelivery)
            .filter_by(task_event_id=task_event.event_id)
            .one()
        )

    pending_delivery, retry_delivery, delivered_delivery, failed_delivery = deliveries
    retry_delivery.status = "failed"
    retry_delivery.outbox_entry.status = "pending"
    delivered_delivery.status = "succeeded"
    delivered_delivery.outbox_entry.status = "delivered"
    failed_delivery.status = "failed"
    failed_delivery.outbox_entry.status = "permanently_failed"
    legacy_failed_delivery = NotificationDelivery(
        channel=channel,
        dedupe_key=f"legacy-{uuid4()}",
        event_type="task_failed",
        status="failed",
    )
    db_session.add(legacy_failed_delivery)
    db_session.commit()

    preserved_ids = {pending_delivery.id, retry_delivery.id}
    delete_notification_channel(db_session, channel.id)

    remaining_deliveries = db_session.query(NotificationDelivery).all()
    assert {delivery.id for delivery in remaining_deliveries} == preserved_ids
    assert all(delivery.channel_id is None for delivery in remaining_deliveries)
    remaining_outboxes = db_session.query(NotificationOutbox).all()
    assert {outbox.delivery_id for outbox in remaining_outboxes} == preserved_ids
    assert all(outbox.status == "pending" for outbox in remaining_outboxes)


def test_outbox_drain_delivers_and_marks_succeeded(db_session, monkeypatch) -> None:
    service, instance = seed_service_and_instance(db_session)
    seed_channel(db_session, event_types=["task_failed"])
    task_event = seed_failed_task_event(db_session, service, instance)

    dispatch_runtime_task_event_notifications(db_session, task_events=[task_event])

    seen_urls: list[str] = []

    def ok_post_webhook(delivery, *, webhook_url: str, timeout_s: float = 5.0) -> None:
        seen_urls.append(webhook_url)
        delivery.status = "succeeded"
        delivery.response_status_code = 200
        delivery.sent_at = _utcnow()

    monkeypatch.setattr(
        "onestep_control_plane_api.api.notification_service._post_webhook",
        ok_post_webhook,
    )

    processed = drain_notification_outbox(db_session, now=_utcnow())
    assert processed == 1

    outbox = db_session.query(NotificationOutbox).one()
    assert outbox.status == "delivered"
    assert outbox.attempts == 1
    assert outbox.last_response_status_code == 200
    delivery = db_session.query(NotificationDelivery).one()
    assert delivery.status == "succeeded"
    assert seen_urls == ["https://example.com/hook"]

    # A subsequent drain finds nothing to do.
    assert drain_notification_outbox(db_session, now=_utcnow()) == 0


def test_outbox_drain_retries_with_backoff_then_marks_permanently_failed(
    db_session, monkeypatch
) -> None:
    """At-least-once delivery: transient failures retry with backoff up to a
    bounded max-attempts, after which the row is permanently failed."""
    service, instance = seed_service_and_instance(db_session)
    seed_channel(db_session, event_types=["task_failed"])
    task_event = seed_failed_task_event(db_session, service, instance)

    dispatch_runtime_task_event_notifications(db_session, task_events=[task_event])

    attempt_count = {"n": 0}

    def failing_post_webhook(delivery, *, webhook_url: str, timeout_s: float = 5.0) -> None:
        attempt_count["n"] += 1
        delivery.status = "failed"
        delivery.error_message = "boom"
        delivery.sent_at = _utcnow()

    monkeypatch.setattr(
        "onestep_control_plane_api.api.notification_service._post_webhook",
        failing_post_webhook,
    )

    outbox = db_session.query(NotificationOutbox).one()
    outbox.max_attempts = 2
    db_session.commit()

    now = _utcnow()
    # First attempt: fails, scheduled for retry with backoff.
    assert drain_notification_outbox(db_session, now=now) == 1
    db_session.refresh(outbox)
    assert outbox.status == "pending"
    assert outbox.attempts == 1
    assert outbox.next_attempt_at > now  # backoff pushes it into the future
    assert attempt_count["n"] == 1

    # Immediately after: backoff not elapsed, nothing is due.
    assert drain_notification_outbox(db_session, now=now) == 0
    assert attempt_count["n"] == 1

    # Advance virtual time well past the backoff: second attempt exhausts the cap.
    future = now + timedelta(hours=1)
    assert drain_notification_outbox(db_session, now=future) == 1
    db_session.refresh(outbox)
    assert outbox.status == "permanently_failed"
    assert outbox.attempts == 2
    assert outbox.last_error == "boom"
    assert attempt_count["n"] == 2

    delivery = db_session.query(NotificationDelivery).one()
    assert delivery.status == "failed"
    assert delivery.error_message == "boom"


def test_outbox_retry_replaces_previous_attempt_diagnostics(db_session, monkeypatch) -> None:
    service, instance = seed_service_and_instance(db_session)
    seed_channel(db_session, event_types=["task_failed"])
    task_event = seed_failed_task_event(db_session, service, instance)
    dispatch_runtime_task_event_notifications(db_session, task_events=[task_event])

    attempt_count = 0

    def deliver_with_changing_outcomes(
        delivery, *, webhook_url: str, timeout_s: float = 5.0
    ) -> None:
        nonlocal attempt_count
        attempt_count += 1
        if attempt_count == 1:
            delivery.status = "failed"
            delivery.response_status_code = 503
            delivery.response_body = "busy"
            delivery.error_message = "webhook responded with status 503"
        elif attempt_count == 2:
            delivery.status = "failed"
            delivery.error_message = "connection reset"
        else:
            delivery.status = "succeeded"
            delivery.response_status_code = 200
            delivery.response_body = "ok"
        delivery.sent_at = _utcnow()

    monkeypatch.setattr(
        "onestep_control_plane_api.api.notification_service._post_webhook",
        deliver_with_changing_outcomes,
    )

    outbox = db_session.query(NotificationOutbox).one()
    outbox.max_attempts = 3
    db_session.commit()

    assert drain_notification_outbox(db_session, now=_utcnow()) == 1
    db_session.refresh(outbox)
    assert outbox.last_response_status_code == 503
    assert outbox.last_response_body == "busy"

    assert (
        drain_notification_outbox(
            db_session,
            now=outbox.next_attempt_at + timedelta(seconds=1),
        )
        == 1
    )
    db_session.refresh(outbox)
    delivery = outbox.delivery
    assert delivery.response_status_code is None
    assert delivery.response_body is None
    assert outbox.last_response_status_code is None
    assert outbox.last_response_body is None
    assert outbox.last_error == "connection reset"

    assert (
        drain_notification_outbox(
            db_session,
            now=outbox.next_attempt_at + timedelta(seconds=1),
        )
        == 1
    )
    db_session.refresh(outbox)
    assert outbox.status == "delivered"
    assert outbox.last_error is None
    assert outbox.last_response_status_code == 200
    assert outbox.last_response_body == "ok"
    assert delivery.error_message is None
    assert delivery.response_status_code == 200
    assert delivery.response_body == "ok"


def test_claim_increments_attempts_for_at_least_once_semantics(db_session) -> None:
    """Claiming a row increments attempts in its own committed transaction so a
    worker crash after claiming leaves the row retriable (at-least-once)."""
    service, instance = seed_service_and_instance(db_session)
    seed_channel(db_session, event_types=["task_failed"])
    task_event = seed_failed_task_event(db_session, service, instance)
    dispatch_runtime_task_event_notifications(db_session, task_events=[task_event])

    now = _utcnow()
    claimed = claim_pending_outbox_rows(db_session, now=now, batch_size=10)
    assert len(claimed) == 1
    assert claimed[0].attempts == 1
    assert claimed[0].last_attempt_at == now

    # The claim committed: a fresh session still sees attempts == 1.
    outbox = db_session.query(NotificationOutbox).one()
    assert outbox.attempts == 1
    assert outbox.status == "pending"


# --------------------------------------------------------------------------- #
# Leader-gated worker: only the leader drains; replicas don't double-fire.     #
# --------------------------------------------------------------------------- #


async def _yield_once(_: float) -> None:
    await asyncio.sleep(0)


async def _wait_until(predicate, *, timeout_s: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("condition was not met before timeout")
        await asyncio.sleep(0)


class SharedLeaseCoordinator:
    def __init__(self) -> None:
        self.owner: str | None = None


class CoordinatedLease(WorkerLease):
    mode = "postgres_advisory_lock"

    def __init__(self, *, coordinator: SharedLeaseCoordinator, replica_id: str) -> None:
        self._coordinator = coordinator
        self._replica_id = replica_id
        self.acquired_at: datetime | None = None
        self.release_count = 0

    def ensure_leader(self) -> bool:
        if self._coordinator.owner in (None, self._replica_id):
            if self._coordinator.owner is None:
                self._coordinator.owner = self._replica_id
                self.acquired_at = datetime.now(UTC)
            return True
        return False

    def release(self) -> None:
        self.release_count += 1
        if self._coordinator.owner == self._replica_id:
            self._coordinator.owner = None
        self.acquired_at = None


def _build_memory_app(session_factory) -> FastAPI:
    app = FastAPI()
    app.state.session_factory = session_factory
    app.state.background_task_states = build_default_background_task_states()
    app.state.background_task_refs = {
        NOTIFICATION_OUTBOX_WORKER_NAME: None,
    }
    return app


def _build_session_factory():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    def factory() -> Session:
        return Session(engine)

    return factory, engine


def _seed_pending_outbox(session_factory) -> None:
    with session_factory() as db:
        service, instance = seed_service_and_instance(db)
        seed_channel(db, event_types=["task_failed"])
        seed_failed_task_event(db, service, instance)
        dispatch_runtime_task_event_notifications(
            db,
            task_events=db.query(TaskEvent).all(),
        )


def test_worker_waits_for_in_flight_drain_before_releasing_lease() -> None:
    drain_started = threading.Event()
    finish_drain = threading.Event()

    def blocking_drain(db: Session) -> int:
        drain_started.set()
        if not finish_drain.wait(timeout=5):
            raise TimeoutError("test drain was not released")
        return 0

    async def scenario() -> None:
        session_factory, engine = _build_session_factory()
        try:
            coordinator = SharedLeaseCoordinator()
            lease = CoordinatedLease(coordinator=coordinator, replica_id="one")
            app = _build_memory_app(session_factory)
            task = asyncio.create_task(
                run_notification_outbox_worker(
                    app,
                    sleep_fn=_yield_once,
                    drain_fn=blocking_drain,
                    lease_factory=lambda: lease,
                    drain_interval_s=0,
                    leader_poll_interval_s=0,
                )
            )

            await _wait_until(drain_started.is_set)
            task.cancel()
            await asyncio.sleep(0)

            assert not task.done()
            assert lease.release_count == 0

            finish_drain.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert lease.release_count == 1
        finally:
            finish_drain.set()
            engine.dispose()

    asyncio.run(scenario())


def test_only_one_replica_drains_outbox_when_leases_contend(monkeypatch) -> None:
    def fast_ok(delivery, *, webhook_url: str, timeout_s: float = 5.0) -> None:
        delivery.status = "succeeded"
        delivery.response_status_code = 200
        delivery.sent_at = _utcnow()

    monkeypatch.setattr(
        "onestep_control_plane_api.api.notification_service._post_webhook",
        fast_ok,
    )

    async def scenario() -> None:
        session_factory, engine = _build_session_factory()
        try:
            _seed_pending_outbox(session_factory)
            coordinator = SharedLeaseCoordinator()
            app_one = _build_memory_app(session_factory)
            app_two = _build_memory_app(session_factory)
            lease_one = CoordinatedLease(coordinator=coordinator, replica_id="one")
            lease_two = CoordinatedLease(coordinator=coordinator, replica_id="two")
            deliver_totals: list[int] = []

            def counting_drain(db: Session) -> int:
                delivered = drain_notification_outbox(db)
                deliver_totals.append(delivered)
                return delivered

            task_one = asyncio.create_task(
                run_notification_outbox_worker(
                    app_one,
                    sleep_fn=_yield_once,
                    drain_fn=counting_drain,
                    lease_factory=lambda: lease_one,
                    drain_interval_s=0,
                    leader_poll_interval_s=0,
                )
            )
            app_one.state.background_task_refs[NOTIFICATION_OUTBOX_WORKER_NAME] = task_one
            task_two = asyncio.create_task(
                run_notification_outbox_worker(
                    app_two,
                    sleep_fn=_yield_once,
                    drain_fn=counting_drain,
                    lease_factory=lambda: lease_two,
                    drain_interval_s=0,
                    leader_poll_interval_s=0,
                )
            )
            app_two.state.background_task_refs[NOTIFICATION_OUTBOX_WORKER_NAME] = task_two

            await _wait_until(
                lambda: sum(deliver_totals) >= 1
                and {
                    app_one.state.background_task_states[
                        NOTIFICATION_OUTBOX_WORKER_NAME
                    ].leadership_status,
                    app_two.state.background_task_states[
                        NOTIFICATION_OUTBOX_WORKER_NAME
                    ].leadership_status,
                }
                == {"leader", "standby"}
            )

            task_one.cancel()
            task_two.cancel()
            for task in (task_one, task_two):
                with pytest.raises(asyncio.CancelledError):
                    await task

            # Exactly one replica drained, and the single pending row was
            # delivered exactly once (no double-fire).
            assert sum(deliver_totals) == 1
            with session_factory() as db:
                outbox = db.query(NotificationOutbox).one()
                assert outbox.status == "delivered"
                assert outbox.attempts == 1
        finally:
            engine.dispose()

    asyncio.run(scenario())


def test_outbox_worker_runs_in_local_mode(monkeypatch) -> None:
    def fast_ok(delivery, *, webhook_url: str, timeout_s: float = 5.0) -> None:
        delivery.status = "succeeded"
        delivery.response_status_code = 200
        delivery.sent_at = _utcnow()

    monkeypatch.setattr(
        "onestep_control_plane_api.api.notification_service._post_webhook",
        fast_ok,
    )

    from onestep_control_plane_api.workers.leader import LocalWorkerLease

    async def scenario() -> None:
        session_factory, engine = _build_session_factory()
        try:
            _seed_pending_outbox(session_factory)
            app = _build_memory_app(session_factory)

            task = asyncio.create_task(
                run_notification_outbox_worker(
                    app,
                    sleep_fn=_yield_once,
                    lease_factory=LocalWorkerLease,
                    drain_interval_s=0,
                    leader_poll_interval_s=0,
                )
            )
            app.state.background_task_refs[NOTIFICATION_OUTBOX_WORKER_NAME] = task

            state = app.state.background_task_states[NOTIFICATION_OUTBOX_WORKER_NAME]
            await _wait_until(lambda: state.last_success_at is not None)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

            assert state.leadership_mode == "local"
            assert state.leadership_status == "leader"
            with session_factory() as db:
                assert db.query(NotificationOutbox).one().status == "delivered"
        finally:
            engine.dispose()

    asyncio.run(scenario())
