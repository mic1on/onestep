from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, foreign, mapped_column, relationship

from onestep_control_plane_api.db.base import Base
from onestep_control_plane_api.db.types import UTCDateTime


def utcnow() -> datetime:
    return datetime.now(UTC)


JSON_TYPE = sa.JSON().with_variant(JSONB(), "postgresql")


class Service(Base):
    __tablename__ = "services"
    __table_args__ = (
        sa.UniqueConstraint("name", "environment", name="uq_services_name_environment"),
        sa.Index("ix_services_environment_name", "environment", "name"),
    )

    id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    environment: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    latest_deployment_version: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    latest_topology_hash: Mapped[str | None] = mapped_column(sa.String(255))
    latest_sync_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
    )

    instances: Mapped[list[Instance]] = relationship(
        back_populates="service",
        cascade="all, delete-orphan",
    )
    task_definitions: Mapped[list[TaskDefinition]] = relationship(
        back_populates="service",
        cascade="all, delete-orphan",
    )
    agent_commands: Mapped[list[AgentCommand]] = relationship(
        back_populates="service",
        cascade="all, delete-orphan",
    )
    agent_sessions: Mapped[list[AgentSession]] = relationship(
        back_populates="service",
        cascade="all, delete-orphan",
    )
    task_metric_windows: Mapped[list[TaskMetricWindow]] = relationship(
        back_populates="service",
        cascade="all, delete-orphan",
    )
    task_events: Mapped[list[TaskEvent]] = relationship(
        back_populates="service",
        cascade="all, delete-orphan",
    )


class Instance(Base):
    __tablename__ = "instances"
    __table_args__ = (
        sa.UniqueConstraint("instance_id", name="uq_instances_instance_id"),
        sa.Index("ix_instances_service_id_last_seen_at", "service_id", "last_seen_at"),
    )

    id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True, default=uuid4)
    service_id: Mapped[UUID] = mapped_column(
        sa.ForeignKey("services.id", ondelete="CASCADE"),
        nullable=False,
    )
    instance_id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), nullable=False)
    node_name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    hostname: Mapped[str | None] = mapped_column(sa.String(255))
    pid: Mapped[int | None] = mapped_column(sa.Integer)
    deployment_version: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    onestep_version: Mapped[str | None] = mapped_column(sa.String(64))
    python_version: Mapped[str | None] = mapped_column(sa.String(64))
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_sync_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_topology_hash: Mapped[str | None] = mapped_column(sa.String(255))
    app_snapshot_json: Mapped[dict[str, object] | None] = mapped_column(JSON_TYPE)
    last_sync_sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_sync_sequence: Mapped[int | None] = mapped_column(sa.Integer)
    last_heartbeat_sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_heartbeat_sequence: Mapped[int | None] = mapped_column(sa.Integer)
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    status: Mapped[str] = mapped_column(sa.String(32), nullable=False, default="unknown")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
    )

    service: Mapped[Service] = relationship(back_populates="instances")
    agent_commands: Mapped[list[AgentCommand]] = relationship(
        back_populates="instance",
        cascade="all, delete-orphan",
        primaryjoin=lambda: Instance.instance_id == foreign(AgentCommand.instance_id),
    )
    agent_sessions: Mapped[list[AgentSession]] = relationship(
        back_populates="instance",
        cascade="all, delete-orphan",
        primaryjoin=lambda: Instance.instance_id == foreign(AgentSession.instance_id),
    )
    task_metric_windows: Mapped[list[TaskMetricWindow]] = relationship(
        back_populates="instance",
        cascade="all, delete-orphan",
        primaryjoin=lambda: Instance.instance_id == foreign(TaskMetricWindow.instance_id),
    )
    task_events: Mapped[list[TaskEvent]] = relationship(
        back_populates="instance",
        cascade="all, delete-orphan",
        primaryjoin=lambda: Instance.instance_id == foreign(TaskEvent.instance_id),
    )


class TaskDefinition(Base):
    __tablename__ = "task_definitions"
    __table_args__ = (
        sa.UniqueConstraint(
            "service_id",
            "task_name",
            name="uq_task_definitions_service_id_task_name",
        ),
    )

    id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True, default=uuid4)
    service_id: Mapped[UUID] = mapped_column(
        sa.ForeignKey("services.id", ondelete="CASCADE"),
        nullable=False,
    )
    task_name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text)
    source_name: Mapped[str | None] = mapped_column(sa.String(255))
    source_kind: Mapped[str | None] = mapped_column(sa.String(128))
    source_config_json: Mapped[dict[str, object] | None] = mapped_column(JSON_TYPE)
    emit_json: Mapped[list[dict[str, object]] | None] = mapped_column(JSON_TYPE)
    concurrency: Mapped[int | None] = mapped_column(sa.Integer)
    timeout_s: Mapped[float | None] = mapped_column(sa.Float)
    retry_policy: Mapped[dict[str, object] | None] = mapped_column(JSON_TYPE)
    topology_hash: Mapped[str | None] = mapped_column(sa.String(255))
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
    )

    service: Mapped[Service] = relationship(back_populates="task_definitions")


class AgentSession(Base):
    __tablename__ = "agent_sessions"
    __table_args__ = (
        sa.UniqueConstraint("session_id", name="uq_agent_sessions_session_id"),
        sa.Index(
            "ix_agent_sessions_instance_id_status_connected_at",
            "instance_id",
            "status",
            "connected_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True, default=uuid4)
    session_id: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    service_id: Mapped[UUID] = mapped_column(
        sa.ForeignKey("services.id", ondelete="CASCADE"),
        nullable=False,
    )
    instance_id: Mapped[UUID] = mapped_column(
        sa.ForeignKey("instances.instance_id", ondelete="CASCADE"),
        nullable=False,
    )
    protocol_version: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    status: Mapped[str] = mapped_column(sa.String(32), nullable=False, default="active")
    capabilities_json: Mapped[list[str]] = mapped_column(JSON_TYPE, nullable=False, default=list)
    accepted_capabilities_json: Mapped[list[str]] = mapped_column(
        JSON_TYPE,
        nullable=False,
        default=list,
    )
    connected_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    last_hello_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    last_message_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    superseded_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    disconnected_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
    )

    service: Mapped[Service] = relationship(back_populates="agent_sessions")
    instance: Mapped[Instance] = relationship(
        back_populates="agent_sessions",
        primaryjoin=lambda: foreign(AgentSession.instance_id) == Instance.instance_id,
    )


class AgentCommand(Base):
    __tablename__ = "agent_commands"
    __table_args__ = (
        sa.UniqueConstraint("command_id", name="uq_agent_commands_command_id"),
        sa.Index(
            "ix_agent_commands_instance_id_status_created_at",
            "instance_id",
            "status",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True, default=uuid4)
    command_id: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    service_id: Mapped[UUID] = mapped_column(
        sa.ForeignKey("services.id", ondelete="CASCADE"),
        nullable=False,
    )
    instance_id: Mapped[UUID] = mapped_column(
        sa.ForeignKey("instances.instance_id", ondelete="CASCADE"),
        nullable=False,
    )
    session_id: Mapped[str | None] = mapped_column(sa.String(255))
    kind: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    args_json: Mapped[dict[str, object]] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    timeout_s: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    status: Mapped[str] = mapped_column(sa.String(32), nullable=False, default="pending")
    ack_status: Mapped[str | None] = mapped_column(sa.String(32))
    result_json: Mapped[dict[str, object] | None] = mapped_column(JSON_TYPE)
    error_code: Mapped[str | None] = mapped_column(sa.String(128))
    error_message: Mapped[str | None] = mapped_column(sa.Text)
    dispatched_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    acked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
    )

    service: Mapped[Service] = relationship(back_populates="agent_commands")
    instance: Mapped[Instance] = relationship(
        back_populates="agent_commands",
        primaryjoin=lambda: foreign(AgentCommand.instance_id) == Instance.instance_id,
    )


class TaskMetricWindow(Base):
    __tablename__ = "task_metric_windows"
    __table_args__ = (
        sa.UniqueConstraint(
            "instance_id",
            "task_name",
            "window_id",
            name="uq_task_metric_windows_instance_id_task_name_window_id",
        ),
        sa.Index(
            "ix_task_metric_windows_service_id_task_name_window_ended_at",
            "service_id",
            "task_name",
            "window_ended_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True, default=uuid4)
    service_id: Mapped[UUID] = mapped_column(
        sa.ForeignKey("services.id", ondelete="CASCADE"),
        nullable=False,
    )
    instance_id: Mapped[UUID] = mapped_column(
        sa.ForeignKey("instances.instance_id", ondelete="CASCADE"),
        nullable=False,
    )
    task_name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    window_id: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    window_started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    window_ended_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    fetched: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    started: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    succeeded: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    retried: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    failed: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    dead_lettered: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    cancelled: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    timeouts: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    inflight: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    avg_duration_ms: Mapped[float | None] = mapped_column(sa.Float)
    p95_duration_ms: Mapped[float | None] = mapped_column(sa.Float)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)

    service: Mapped[Service] = relationship(back_populates="task_metric_windows")
    instance: Mapped[Instance] = relationship(
        back_populates="task_metric_windows",
        primaryjoin=lambda: foreign(TaskMetricWindow.instance_id) == Instance.instance_id,
    )


class TaskEvent(Base):
    __tablename__ = "task_events"
    __table_args__ = (
        sa.UniqueConstraint("event_id", name="uq_task_events_event_id"),
        sa.Index(
            "ix_task_events_service_id_task_name_occurred_at",
            "service_id",
            "task_name",
            "occurred_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True, default=uuid4)
    event_id: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    service_id: Mapped[UUID] = mapped_column(
        sa.ForeignKey("services.id", ondelete="CASCADE"),
        nullable=False,
    )
    instance_id: Mapped[UUID] = mapped_column(
        sa.ForeignKey("instances.instance_id", ondelete="CASCADE"),
        nullable=False,
    )
    task_name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    kind: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    attempts: Mapped[int | None] = mapped_column(sa.Integer)
    duration_ms: Mapped[int | None] = mapped_column(sa.Integer)
    failure_kind: Mapped[str | None] = mapped_column(sa.String(64))
    exception_type: Mapped[str | None] = mapped_column(sa.String(255))
    message: Mapped[str | None] = mapped_column(sa.Text)
    traceback: Mapped[str | None] = mapped_column(sa.Text)
    meta_json: Mapped[dict[str, object]] = mapped_column(
        JSON_TYPE,
        nullable=False,
        default=dict,
    )
    received_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)

    service: Mapped[Service] = relationship(back_populates="task_events")
    instance: Mapped[Instance] = relationship(
        back_populates="task_events",
        primaryjoin=lambda: foreign(TaskEvent.instance_id) == Instance.instance_id,
    )
