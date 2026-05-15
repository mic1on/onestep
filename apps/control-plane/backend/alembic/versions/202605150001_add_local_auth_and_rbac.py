"""Add local auth and RBAC foundation tables.

Revision ID: 202605150001
Revises: 202604300001
Create Date: 2026-05-15 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from onestep_control_plane_api.db.types import UTCDateTime

# revision identifiers, used by Alembic.
revision: str = "202605150001"
down_revision: str | None = "202604300001"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _table_exists(table_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return table_name in set(inspector.get_table_names())


def utcnow() -> datetime:
    return datetime.now(UTC)


def upgrade() -> None:
    if not _table_exists("local_roles"):
        op.create_table(
            "local_roles",
            sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
            sa.Column("name", sa.String(length=32), nullable=False),
            sa.Column("created_at", UTCDateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_local_roles")),
            sa.UniqueConstraint("name", name="uq_local_roles_name"),
        )

    if not _table_exists("local_users"):
        op.create_table(
            "local_users",
            sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
            sa.Column("username", sa.String(length=255), nullable=False),
            sa.Column("password_hash", sa.Text(), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", UTCDateTime(), nullable=False),
            sa.Column("updated_at", UTCDateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_local_users")),
            sa.UniqueConstraint("username", name="uq_local_users_username"),
        )
        op.create_index("ix_local_users_username", "local_users", ["username"], unique=False)

    if not _table_exists("local_user_roles"):
        op.create_table(
            "local_user_roles",
            sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
            sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=False),
            sa.Column("role_id", sa.Uuid(as_uuid=True), nullable=False),
            sa.Column("created_at", UTCDateTime(), nullable=False),
            sa.ForeignKeyConstraint(
                ["role_id"],
                ["local_roles.id"],
                name=op.f("fk_local_user_roles_role_id_local_roles"),
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["user_id"],
                ["local_users.id"],
                name=op.f("fk_local_user_roles_user_id_local_users"),
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_local_user_roles")),
            sa.UniqueConstraint("user_id", "role_id", name="uq_local_user_roles_user_id_role_id"),
        )
        op.create_index(
            "ix_local_user_roles_role_id",
            "local_user_roles",
            ["role_id"],
            unique=False,
        )
        op.create_index(
            "ix_local_user_roles_user_id",
            "local_user_roles",
            ["user_id"],
            unique=False,
        )

    if not _table_exists("console_sessions"):
        op.create_table(
            "console_sessions",
            sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
            sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=False),
            sa.Column("token_hash", sa.String(length=64), nullable=False),
            sa.Column("expires_at", UTCDateTime(), nullable=False),
            sa.Column("last_seen_at", UTCDateTime(), nullable=False),
            sa.Column("revoked_at", UTCDateTime(), nullable=True),
            sa.Column("created_at", UTCDateTime(), nullable=False),
            sa.ForeignKeyConstraint(
                ["user_id"],
                ["local_users.id"],
                name=op.f("fk_console_sessions_user_id_local_users"),
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_console_sessions")),
            sa.UniqueConstraint("token_hash", name="uq_console_sessions_token_hash"),
        )
        op.create_index(
            "ix_console_sessions_user_id_expires_at",
            "console_sessions",
            ["user_id", "expires_at"],
            unique=False,
        )

    roles_table = sa.table(
        "local_roles",
        sa.column("id", sa.Uuid(as_uuid=True)),
        sa.column("name", sa.String(length=32)),
        sa.column("created_at", UTCDateTime()),
    )
    bind = op.get_bind()
    existing_names = {
        row[0] for row in bind.execute(sa.select(roles_table.c.name)).all()
    }
    created_at = utcnow()
    rows = [
        {
            "id": uuid4(),
            "name": role_name,
            "created_at": created_at,
        }
        for role_name in ("viewer", "operator", "admin")
        if role_name not in existing_names
    ]
    if rows:
        op.bulk_insert(roles_table, rows)


def downgrade() -> None:
    if _table_exists("console_sessions"):
        op.drop_index("ix_console_sessions_user_id_expires_at", table_name="console_sessions")
        op.drop_table("console_sessions")
    if _table_exists("local_user_roles"):
        op.drop_index("ix_local_user_roles_user_id", table_name="local_user_roles")
        op.drop_index("ix_local_user_roles_role_id", table_name="local_user_roles")
        op.drop_table("local_user_roles")
    if _table_exists("local_users"):
        op.drop_index("ix_local_users_username", table_name="local_users")
        op.drop_table("local_users")
    if _table_exists("local_roles"):
        op.drop_table("local_roles")
