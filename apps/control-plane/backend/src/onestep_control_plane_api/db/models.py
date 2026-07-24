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
    description: Mapped[str | None] = mapped_column(sa.Text)
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
    task_custom_metric_windows: Mapped[list[TaskCustomMetricWindow]] = relationship(
        back_populates="service",
        cascade="all, delete-orphan",
    )
    task_events: Mapped[list[TaskEvent]] = relationship(
        back_populates="service",
        cascade="all, delete-orphan",
    )


class LocalRole(Base):
    __tablename__ = "local_roles"
    __table_args__ = (sa.UniqueConstraint("name", name="uq_local_roles_name"),)

    id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)

    user_links: Mapped[list[LocalUserRole]] = relationship(
        back_populates="role",
        cascade="all, delete-orphan",
    )


class LocalUser(Base):
    __tablename__ = "local_users"
    __table_args__ = (
        sa.UniqueConstraint("username", name="uq_local_users_username"),
        sa.Index("ix_local_users_username", "username"),
    )

    id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True, default=uuid4)
    username: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(sa.Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
    )

    role_links: Mapped[list[LocalUserRole]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    console_sessions: Mapped[list[ConsoleSession]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )


class LocalUserRole(Base):
    __tablename__ = "local_user_roles"
    __table_args__ = (
        sa.UniqueConstraint("user_id", "role_id", name="uq_local_user_roles_user_id_role_id"),
        sa.Index("ix_local_user_roles_user_id", "user_id"),
        sa.Index("ix_local_user_roles_role_id", "role_id"),
    )

    id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        sa.ForeignKey("local_users.id", ondelete="CASCADE"),
        nullable=False,
    )
    role_id: Mapped[UUID] = mapped_column(
        sa.ForeignKey("local_roles.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)

    user: Mapped[LocalUser] = relationship(back_populates="role_links")
    role: Mapped[LocalRole] = relationship(back_populates="user_links")


class ConsoleSession(Base):
    __tablename__ = "console_sessions"
    __table_args__ = (
        sa.UniqueConstraint("token_hash", name="uq_console_sessions_token_hash"),
        sa.Index("ix_console_sessions_user_id_expires_at", "user_id", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        sa.ForeignKey("local_users.id", ondelete="CASCADE"),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    authenticated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)

    user: Mapped[LocalUser] = relationship(back_populates="console_sessions")


class ConsoleLoginThrottle(Base):
    __tablename__ = "console_login_throttles"
    __table_args__ = (sa.Index("ix_console_login_throttles_locked_until", "locked_until"),)

    username: Mapped[str] = mapped_column(sa.String(255), primary_key=True)
    failure_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    window_started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
    )


class NotificationChannel(Base):
    __tablename__ = "notification_channels"
    __table_args__ = (sa.UniqueConstraint("name", name="uq_notification_channels_name"),)

    id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    provider: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    webhook_url: Mapped[str] = mapped_column(sa.Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)
    service_scopes_json: Mapped[list[dict[str, object]]] = mapped_column(
        JSON_TYPE,
        nullable=False,
        default=list,
    )
    event_types_json: Mapped[list[str]] = mapped_column(JSON_TYPE, nullable=False, default=list)
    missed_start_grace_seconds: Mapped[int] = mapped_column(
        sa.Integer,
        nullable=False,
        default=300,
    )
    custom_config_json: Mapped[dict[str, object] | None] = mapped_column(JSON_TYPE)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
    )

    deliveries: Mapped[list[NotificationDelivery]] = relationship(
        back_populates="channel",
        cascade="all, delete-orphan",
    )
    instance_states: Mapped[list[NotificationInstanceState]] = relationship(
        back_populates="channel",
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
        sa.Index("ix_agent_commands_status_updated_at", "status", "updated_at"),
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
    created_by: Mapped[str | None] = mapped_column(sa.String(255))
    reason: Mapped[str | None] = mapped_column(sa.Text)
    source_surface: Mapped[str] = mapped_column(
        sa.String(64),
        nullable=False,
        default="unknown",
    )
    kind: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    args_json: Mapped[dict[str, object]] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    timeout_s: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    status: Mapped[str] = mapped_column(sa.String(32), nullable=False, default="pending")
    ack_status: Mapped[str | None] = mapped_column(sa.String(32))
    result_json: Mapped[dict[str, object] | None] = mapped_column(JSON_TYPE)
    duration_ms: Mapped[int | None] = mapped_column(sa.Integer)
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


class WorkerAgent(Base):
    __tablename__ = "worker_agents"
    __table_args__ = (
        sa.UniqueConstraint("worker_agent_id", name="uq_worker_agents_worker_agent_id"),
        sa.Index("ix_worker_agents_status_last_seen_at", "status", "last_seen_at"),
    )

    id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True, default=uuid4)
    worker_agent_id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), nullable=False)
    display_name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    status: Mapped[str] = mapped_column(sa.String(32), nullable=False, default="offline")
    execution_mode: Mapped[str] = mapped_column(sa.String(32), nullable=False, default="subprocess")
    max_concurrent_deployments: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    used_slots: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    labels_json: Mapped[dict[str, str]] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    capabilities_json: Mapped[list[str]] = mapped_column(JSON_TYPE, nullable=False, default=list)
    agent_version: Mapped[str | None] = mapped_column(sa.String(64))
    onestep_version: Mapped[str | None] = mapped_column(sa.String(64))
    python_version: Mapped[str | None] = mapped_column(sa.String(64))
    platform_json: Mapped[dict[str, object]] = mapped_column(
        JSON_TYPE,
        nullable=False,
        default=dict,
    )
    connection_token_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    registered_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
    )

    sessions: Mapped[list[WorkerAgentSession]] = relationship(
        back_populates="worker_agent",
        cascade="all, delete-orphan",
    )
    deployments: Mapped[list[WorkerDeployment]] = relationship(
        back_populates="worker_agent",
        cascade="all, delete-orphan",
    )


class WorkerAgentSession(Base):
    __tablename__ = "worker_agent_sessions"
    __table_args__ = (
        sa.UniqueConstraint("session_id", name="uq_worker_agent_sessions_session_id"),
        sa.Index(
            "ix_worker_agent_sessions_agent_status_connected_at",
            "worker_agent_id",
            "status",
            "connected_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True, default=uuid4)
    session_id: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    worker_agent_id: Mapped[UUID] = mapped_column(
        sa.ForeignKey("worker_agents.worker_agent_id", ondelete="CASCADE"),
        nullable=False,
    )
    protocol_version: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    status: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    capabilities_json: Mapped[list[str]] = mapped_column(JSON_TYPE, nullable=False, default=list)
    accepted_capabilities_json: Mapped[list[str]] = mapped_column(
        JSON_TYPE,
        nullable=False,
        default=list,
    )
    connected_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    last_hello_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    last_message_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    disconnected_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
    )

    worker_agent: Mapped[WorkerAgent] = relationship(back_populates="sessions")


class WorkflowPackage(Base):
    __tablename__ = "workflow_packages"
    __table_args__ = (
        sa.UniqueConstraint("package_id", name="uq_workflow_packages_package_id"),
        sa.Index("ix_workflow_packages_workflow_id_created_at", "workflow_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True, default=uuid4)
    package_id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), nullable=False)
    workflow_id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), nullable=False)
    version: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    filename: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    storage_path: Mapped[str] = mapped_column(sa.Text, nullable=False)
    entrypoint: Mapped[str] = mapped_column(sa.String(255), nullable=False, default="worker.yaml")
    metadata_json: Mapped[dict[str, object]] = mapped_column(
        JSON_TYPE,
        nullable=False,
        default=dict,
    )
    created_by: Mapped[str] = mapped_column(sa.String(255), nullable=False, default="system")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)

    deployments: Mapped[list[WorkerDeployment]] = relationship(back_populates="workflow_package")


class WorkerDeployment(Base):
    __tablename__ = "worker_deployments"
    __table_args__ = (
        sa.UniqueConstraint("deployment_id", name="uq_worker_deployments_deployment_id"),
        sa.Index(
            "ix_worker_deployments_agent_observed_status",
            "worker_agent_id",
            "observed_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True, default=uuid4)
    deployment_id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), nullable=False)
    workflow_package_id: Mapped[UUID] = mapped_column(
        sa.ForeignKey("workflow_packages.package_id", ondelete="RESTRICT"),
        nullable=False,
    )
    worker_agent_id: Mapped[UUID] = mapped_column(
        sa.ForeignKey("worker_agents.worker_agent_id", ondelete="RESTRICT"),
        nullable=False,
    )
    desired_status: Mapped[str] = mapped_column(sa.String(32), nullable=False, default="running")
    observed_status: Mapped[str] = mapped_column(sa.String(32), nullable=False, default="pending")
    runtime_instance_id: Mapped[UUID | None] = mapped_column(sa.Uuid(as_uuid=True))
    execution_mode: Mapped[str] = mapped_column(sa.String(32), nullable=False, default="subprocess")
    params_json: Mapped[dict[str, object]] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    env_json: Mapped[dict[str, str]] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    credential_refs_json: Mapped[list[str]] = mapped_column(JSON_TYPE, nullable=False, default=list)
    package_checksum: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    last_error_code: Mapped[str | None] = mapped_column(sa.String(128))
    last_error_message: Mapped[str | None] = mapped_column(sa.Text)
    assigned_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_by: Mapped[str] = mapped_column(sa.String(255), nullable=False, default="system")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
    )

    worker_agent: Mapped[WorkerAgent] = relationship(back_populates="deployments")
    workflow_package: Mapped[WorkflowPackage] = relationship(back_populates="deployments")


class WorkerAgentCommand(Base):
    __tablename__ = "worker_agent_commands"
    __table_args__ = (
        sa.UniqueConstraint("command_id", name="uq_worker_agent_commands_command_id"),
        sa.Index("ix_worker_agent_commands_agent_status", "worker_agent_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True, default=uuid4)
    command_id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), nullable=False)
    worker_agent_id: Mapped[UUID] = mapped_column(
        sa.ForeignKey("worker_agents.worker_agent_id", ondelete="CASCADE"),
        nullable=False,
    )
    deployment_id: Mapped[UUID | None] = mapped_column(sa.Uuid(as_uuid=True))
    session_id: Mapped[str | None] = mapped_column(sa.String(255))
    kind: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    args_json: Mapped[dict[str, object]] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    timeout_s: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    status: Mapped[str] = mapped_column(sa.String(32), nullable=False, default="pending")
    ack_status: Mapped[str | None] = mapped_column(sa.String(32))
    result_json: Mapped[dict[str, object] | None] = mapped_column(JSON_TYPE)
    error_code: Mapped[str | None] = mapped_column(sa.String(128))
    error_message: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    dispatched_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    acked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
    )


class WorkerDeploymentEvent(Base):
    __tablename__ = "worker_deployment_events"
    __table_args__ = (
        sa.Index(
            "ix_worker_deployment_events_deployment_created_at",
            "deployment_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True, default=uuid4)
    deployment_id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), nullable=False)
    worker_agent_id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    observed_status: Mapped[str | None] = mapped_column(sa.String(32))
    message: Mapped[str] = mapped_column(sa.Text, nullable=False, default="")
    payload_json: Mapped[dict[str, object]] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)


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
        sa.Index("ix_task_metric_windows_window_ended_at", "window_ended_at"),
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


class TaskCustomMetricWindow(Base):
    __tablename__ = "task_custom_metric_windows"
    __table_args__ = (
        sa.UniqueConstraint(
            "instance_id",
            "task_name",
            "window_id",
            "metric_name",
            "metric_kind",
            "labels_hash",
            name="uq_task_custom_metric_windows_series_window",
        ),
        sa.Index(
            "ix_task_custom_metric_windows_service_window",
            "service_id",
            "window_ended_at",
        ),
        sa.Index(
            "ix_task_custom_metric_windows_metric_kind",
            "metric_name",
            "metric_kind",
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
    metric_name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    metric_kind: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    metric_value: Mapped[float] = mapped_column(sa.Float, nullable=False)
    labels_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    labels_json: Mapped[dict[str, object]] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)

    service: Mapped[Service] = relationship(back_populates="task_custom_metric_windows")
    instance: Mapped[Instance] = relationship(
        primaryjoin=lambda: foreign(TaskCustomMetricWindow.instance_id) == Instance.instance_id,
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
        sa.Index("ix_task_events_occurred_at", "occurred_at"),
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


class NotificationDelivery(Base):
    __tablename__ = "notification_deliveries"
    __table_args__ = (
        sa.UniqueConstraint("dedupe_key", name="uq_notification_deliveries_dedupe_key"),
        sa.Index(
            "ix_notification_deliveries_channel_id_created_at",
            "channel_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True, default=uuid4)
    channel_id: Mapped[UUID] = mapped_column(
        sa.ForeignKey("notification_channels.id", ondelete="CASCADE"),
        nullable=False,
    )
    dedupe_key: Mapped[str] = mapped_column(sa.String(512), nullable=False)
    event_type: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    service_name: Mapped[str | None] = mapped_column(sa.String(255))
    service_environment: Mapped[str | None] = mapped_column(sa.String(32))
    task_name: Mapped[str | None] = mapped_column(sa.String(255))
    task_event_id: Mapped[str | None] = mapped_column(sa.String(255))
    scheduled_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    status: Mapped[str] = mapped_column(sa.String(32), nullable=False, default="pending")
    request_payload_json: Mapped[dict[str, object] | None] = mapped_column(JSON_TYPE)
    response_status_code: Mapped[int | None] = mapped_column(sa.Integer)
    response_body: Mapped[str | None] = mapped_column(sa.Text)
    error_message: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    channel: Mapped[NotificationChannel] = relationship(back_populates="deliveries")


class NotificationInstanceState(Base):
    __tablename__ = "notification_instance_states"
    __table_args__ = (
        sa.UniqueConstraint(
            "channel_id",
            "instance_id",
            name="uq_notification_instance_states_channel_id_instance_id",
        ),
    )

    id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True, default=uuid4)
    channel_id: Mapped[UUID] = mapped_column(
        sa.ForeignKey("notification_channels.id", ondelete="CASCADE"),
        nullable=False,
    )
    instance_id: Mapped[UUID] = mapped_column(
        sa.ForeignKey("instances.instance_id", ondelete="CASCADE"),
        nullable=False,
    )
    last_connectivity: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    last_transition_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
    )

    channel: Mapped[NotificationChannel] = relationship(back_populates="instance_states")


class Connector(Base):
    __tablename__ = "connectors"
    __table_args__ = (
        sa.UniqueConstraint("name", name="uq_connectors_name"),
        sa.Index("ix_connectors_type", "type"),
    )

    id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    type: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    config_json: Mapped[dict[str, object]] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    secret_encrypted: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
    )


class Worker(Base):
    __tablename__ = "workers"
    __table_args__ = (
        sa.UniqueConstraint("name", name="uq_workers_name"),
    )

    id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    description: Mapped[str] = mapped_column(sa.Text, nullable=False, default="")
    handler_package_id: Mapped[UUID | None] = mapped_column(sa.Uuid(as_uuid=True), nullable=True)
    handler_ref: Mapped[str] = mapped_column(
        sa.String(255),
        nullable=False,
        default="handler:handler",
    )
    source_config: Mapped[dict[str, object]] = mapped_column(
        JSON_TYPE,
        nullable=False,
        default=dict,
    )
    sink_configs: Mapped[list[object]] = mapped_column(JSON_TYPE, nullable=False, default=list)
    env_json: Mapped[dict[str, str]] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    reporting_enabled: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)
    reporting_config_json: Mapped[dict[str, object]] = mapped_column(
        JSON_TYPE,
        nullable=False,
        default=dict,
    )
    reporting_secret_encrypted: Mapped[str | None] = mapped_column(sa.Text)
    status: Mapped[str] = mapped_column(sa.String(32), nullable=False, default="draft")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
    )
