from __future__ import annotations

from datetime import timedelta

from onestep_control_plane_api.api.common import utcnow
from onestep_control_plane_api.auth.passwords import hash_password, verify_password
from onestep_control_plane_api.auth.service import LocalAuthError, LocalAuthService
from onestep_control_plane_api.db.models import ConsoleSession, LocalRole
from sqlalchemy import select


def test_password_hashing_round_trip() -> None:
    password_hash = hash_password("secret-pass")

    assert password_hash != "secret-pass"
    assert verify_password("secret-pass", password_hash) is True
    assert verify_password("wrong-pass", password_hash) is False


def test_hash_password_rejects_short_password() -> None:
    try:
        hash_password("short")
    except ValueError as exc:
        assert "at least 10 characters" in str(exc)
    else:
        raise AssertionError("expected short password to be rejected")


def test_ensure_builtin_roles_is_idempotent(db_session) -> None:
    service = LocalAuthService(db_session)

    first = service.ensure_builtin_roles()
    second = service.ensure_builtin_roles()

    assert [role.name for role in first] == ["viewer", "operator", "admin"]
    assert [role.name for role in second] == ["viewer", "operator", "admin"]
    assert db_session.scalar(select(LocalRole).where(LocalRole.name == "admin")) is not None


def test_create_user_persists_hashed_password_and_roles(db_session) -> None:
    service = LocalAuthService(db_session)

    identity = service.create_user(
        username="Admin",
        password="secret-pass",
        role_names=["admin", "viewer"],
    )

    stored_user = service.get_user_by_username("admin")
    assert stored_user is not None
    assert stored_user.username == "admin"
    assert stored_user.password_hash != "secret-pass"
    assert verify_password("secret-pass", stored_user.password_hash) is True
    assert identity.username == "admin"
    assert identity.roles == ("admin", "viewer")


def test_create_user_rejects_duplicate_username(db_session) -> None:
    service = LocalAuthService(db_session)
    service.create_user(username="admin", password="secret-pass", role_names=["admin"])

    try:
        service.create_user(username="ADMIN", password="secret-pass", role_names=["viewer"])
    except LocalAuthError as exc:
        assert "user already exists" in str(exc)
    else:
        raise AssertionError("expected duplicate username to be rejected")


def test_create_user_rejects_short_password(db_session) -> None:
    service = LocalAuthService(db_session)

    try:
        service.create_user(username="admin", password="short", role_names=["admin"])
    except ValueError as exc:
        assert "at least 10 characters" in str(exc)
    else:
        raise AssertionError("expected short password to be rejected")


def test_authenticate_user_rejects_wrong_password_and_inactive_user(db_session) -> None:
    service = LocalAuthService(db_session)
    service.create_user(username="viewer", password="secret-pass", role_names=["viewer"])

    assert service.authenticate_user(username="viewer", password="wrong-pass") is None

    user = service.get_user_by_username("viewer")
    assert user is not None
    user.is_active = False
    db_session.commit()

    assert service.authenticate_user(username="viewer", password="secret-pass") is None


def test_console_session_round_trip_and_revocation(db_session) -> None:
    service = LocalAuthService(db_session)
    identity = service.create_user(
        username="operator",
        password="secret-pass",
        role_names=["operator"],
    )

    session = service.create_console_session(identity)
    authenticated = service.authenticate_console_session(session.token)

    assert authenticated is not None
    assert authenticated.username == "operator"
    assert authenticated.roles == ("operator",)
    assert service.revoke_console_session(session.token) is True
    assert service.authenticate_console_session(session.token) is None
    assert service.revoke_console_session(session.token) is False


def test_create_console_session_rotates_existing_user_sessions(db_session) -> None:
    service = LocalAuthService(db_session)
    identity = service.create_user(username="admin", password="secret-pass", role_names=["admin"])

    first = service.create_console_session(identity)
    second = service.create_console_session(identity)

    records = db_session.scalars(
        select(ConsoleSession)
        .where(ConsoleSession.user_id == identity.user_id)
        .order_by(ConsoleSession.created_at)
    ).all()

    assert len(records) == 2
    assert first.token != second.token
    assert records[0].authenticated_at == records[0].created_at
    assert records[0].revoked_at is not None
    assert records[1].authenticated_at == records[1].created_at
    assert records[1].revoked_at is None
    assert service.authenticate_console_session(first.token) is None
    assert service.authenticate_console_session(second.token) is not None


def test_console_session_expiry_and_bulk_revocation(db_session) -> None:
    service = LocalAuthService(db_session)
    identity = service.create_user(username="admin", password="secret-pass", role_names=["admin"])
    first = service.create_console_session(identity)
    second = service.create_console_session(identity)

    first_record = db_session.scalar(
        select(ConsoleSession).where(ConsoleSession.token_hash.is_not(None)).order_by(ConsoleSession.created_at)
    )
    assert first_record is not None
    first_record.expires_at = utcnow() - timedelta(seconds=1)
    db_session.commit()

    assert service.authenticate_console_session(first.token) is None
    assert service.authenticate_console_session(second.token) is not None
    assert service.revoke_user_sessions(identity.user_id) == 1
    assert service.authenticate_console_session(second.token) is None
