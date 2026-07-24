from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from onestep_control_plane_api.db.models import (
    AgentCommand,
    Instance,
    Service,
    TaskEvent,
    TaskMetricWindow,
)
from onestep_control_plane_api.ops.retention import run_retention


def utcnow() -> datetime:
    return datetime.now(UTC)


def seed_service_and_instance(db_session):
    now = utcnow()
    service = Service(
        name="billing-worker",
        environment="prod",
        latest_deployment_version="2026.05.15",
        created_at=now,
        updated_at=now,
    )
    db_session.add(service)
    db_session.flush()
    instance = Instance(
        service_id=service.id,
        instance_id=uuid4(),
        node_name="billing-worker-1",
        deployment_version="2026.05.15",
        status="online",
        created_at=now,
        updated_at=now,
    )
    db_session.add(instance)
    db_session.commit()
    return service, instance


def seed_old_task_event(
    db_session,
    *,
    service: Service,
    instance: Instance,
    occurred_at: datetime,
) -> None:
    db_session.add(
        TaskEvent(
            event_id=f"evt_{uuid4().hex}",
            service_id=service.id,
            instance_id=instance.instance_id,
            task_name="sync_invoice",
            kind="failed",
            occurred_at=occurred_at,
            received_at=occurred_at,
            created_at=occurred_at,
            meta_json={},
        )
    )


def seed_metric_window(
    db_session,
    *,
    service: Service,
    instance: Instance,
    ended_at: datetime,
) -> None:
    started_at = ended_at - timedelta(minutes=5)
    db_session.add(
        TaskMetricWindow(
            service_id=service.id,
            instance_id=instance.instance_id,
            task_name="sync_invoice",
            window_id=f"window_{uuid4().hex}",
            window_started_at=started_at,
            window_ended_at=ended_at,
            fetched=1,
            started=1,
            succeeded=1,
            retried=0,
            failed=0,
            dead_lettered=0,
            cancelled=0,
            timeouts=0,
            inflight=0,
            avg_duration_ms=100.0,
            p95_duration_ms=120.0,
            received_at=ended_at,
            created_at=ended_at,
        )
    )


def seed_agent_command(
    db_session,
    *,
    service: Service,
    instance: Instance,
    status: str,
    updated_at: datetime,
) -> None:
    db_session.add(
        AgentCommand(
            command_id=f"cmd_{uuid4().hex}",
            service_id=service.id,
            instance_id=instance.instance_id,
            created_by="admin",
            source_surface="instance_detail",
            kind="restart",
            args_json={},
            timeout_s=30,
            status=status,
            updated_at=updated_at,
            created_at=updated_at,
            finished_at=updated_at if status != "pending" else None,
        )
    )


def test_run_retention_dry_run_with_no_data(db_session) -> None:
    report = run_retention(db_session, execute=False, now=utcnow())

    assert report.mode == "dry_run"
    assert report.matched_rows == 0
    assert report.deleted_rows == 0
    assert {table.table_name for table in report.tables} == {
        "task_events",
        "task_metric_windows",
        "agent_commands",
    }


def test_run_retention_execute_deletes_only_expired_rows(db_session) -> None:
    service, instance = seed_service_and_instance(db_session)
    now = utcnow()
    old_time = now - timedelta(days=120)
    fresh_time = now - timedelta(days=2)

    seed_old_task_event(db_session, service=service, instance=instance, occurred_at=old_time)
    seed_old_task_event(db_session, service=service, instance=instance, occurred_at=fresh_time)
    seed_metric_window(db_session, service=service, instance=instance, ended_at=old_time)
    seed_metric_window(db_session, service=service, instance=instance, ended_at=fresh_time)
    seed_agent_command(
        db_session,
        service=service,
        instance=instance,
        status="succeeded",
        updated_at=old_time,
    )
    seed_agent_command(
        db_session,
        service=service,
        instance=instance,
        status="pending",
        updated_at=old_time,
    )
    db_session.commit()

    dry_run = run_retention(db_session, execute=False, now=now)
    assert {table.table_name: table.matched_rows for table in dry_run.tables} == {
        "task_events": 1,
        "task_metric_windows": 1,
        "agent_commands": 1,
    }

    execute_report = run_retention(db_session, execute=True, now=now, batch_size=2)
    assert execute_report.deleted_rows == 3
    assert db_session.query(TaskEvent).count() == 1
    assert db_session.query(TaskMetricWindow).count() == 1
    assert db_session.query(AgentCommand).count() == 1
    assert db_session.query(AgentCommand).one().status == "pending"


def test_run_retention_execute_processes_multiple_batches(db_session) -> None:
    service, instance = seed_service_and_instance(db_session)
    now = utcnow()
    old_time = now - timedelta(days=120)
    for _ in range(5):
        seed_old_task_event(db_session, service=service, instance=instance, occurred_at=old_time)
    db_session.commit()

    report = run_retention(db_session, execute=True, now=now, batch_size=2)
    task_events_report = next(table for table in report.tables if table.table_name == "task_events")

    assert task_events_report.matched_rows == 5
    assert task_events_report.deleted_rows == 5
    assert task_events_report.batches == 3
    assert db_session.query(TaskEvent).count() == 0
