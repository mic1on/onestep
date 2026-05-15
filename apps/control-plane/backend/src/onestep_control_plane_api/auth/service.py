from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from onestep_control_plane_api.auth.passwords import hash_password, verify_password
from onestep_control_plane_api.core.settings import settings
from onestep_control_plane_api.db.models import (
    ConsoleSession,
    LocalRole,
    LocalUser,
    LocalUserRole,
)

LOCAL_ROLE_NAMES = ("viewer", "operator", "admin")


class LocalAuthError(ValueError):
    pass


@dataclass(frozen=True)
class LocalIdentity:
    user_id: object
    username: str
    roles: tuple[str, ...]


@dataclass(frozen=True)
class LocalConsoleSession:
    token: str
    identity: LocalIdentity
    expires_at: object


@dataclass(frozen=True)
class LocalAuthenticatedConsoleSession:
    identity: LocalIdentity
    authenticated_at: datetime
    expires_at: object


def utcnow() -> datetime:
    return datetime.now(UTC)


def _hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _normalize_username(username: str) -> str:
    normalized = username.strip().lower()
    if not normalized:
        raise LocalAuthError("username must not be blank")
    return normalized


def _normalize_roles(role_names: list[str] | tuple[str, ...] | set[str]) -> tuple[str, ...]:
    normalized = []
    seen = set()
    for role_name in role_names:
        candidate = str(role_name).strip().lower()
        if candidate not in LOCAL_ROLE_NAMES:
            raise LocalAuthError(f"unsupported role: {role_name}")
        if candidate in seen:
            continue
        seen.add(candidate)
        normalized.append(candidate)
    if not normalized:
        raise LocalAuthError("at least one role is required")
    return tuple(normalized)


class LocalAuthService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def ensure_builtin_roles(self) -> list[LocalRole]:
        existing = {
            role.name: role
            for role in self._db.scalars(
                select(LocalRole).where(LocalRole.name.in_(LOCAL_ROLE_NAMES))
            ).all()
        }
        created = False
        for role_name in LOCAL_ROLE_NAMES:
            if role_name in existing:
                continue
            role = LocalRole(name=role_name)
            self._db.add(role)
            existing[role_name] = role
            created = True
        if created:
            self._db.commit()
            for role in existing.values():
                self._db.refresh(role)
        return [existing[role_name] for role_name in LOCAL_ROLE_NAMES]

    def create_user(
        self,
        *,
        username: str,
        password: str,
        role_names: list[str] | tuple[str, ...] | set[str],
    ) -> LocalIdentity:
        normalized_username = _normalize_username(username)
        normalized_roles = _normalize_roles(role_names)
        existing = self._db.scalar(
            select(LocalUser).where(LocalUser.username == normalized_username)
        )
        if existing is not None:
            raise LocalAuthError(f"user already exists: {normalized_username}")
        roles_by_name = {
            role.name: role for role in self.ensure_builtin_roles()
        }
        user = LocalUser(
            username=normalized_username,
            password_hash=hash_password(password),
            is_active=True,
        )
        self._db.add(user)
        self._db.flush()
        for role_name in normalized_roles:
            self._db.add(LocalUserRole(user_id=user.id, role_id=roles_by_name[role_name].id))
        self._db.commit()
        self._db.refresh(user)
        return self.build_identity(user)

    def authenticate_user(self, *, username: str, password: str) -> LocalIdentity | None:
        normalized_username = _normalize_username(username)
        user = self._db.scalar(select(LocalUser).where(LocalUser.username == normalized_username))
        if user is None or not user.is_active:
            return None
        if not verify_password(password, user.password_hash):
            return None
        return self.build_identity(user)

    def create_console_session(self, identity: LocalIdentity) -> LocalConsoleSession:
        token = secrets.token_urlsafe(32)
        now = utcnow()
        expires_at = now + timedelta(seconds=settings.console_auth_session_ttl_s)
        active_sessions = self._db.scalars(
            select(ConsoleSession).where(
                ConsoleSession.user_id == identity.user_id,
                ConsoleSession.revoked_at.is_(None),
                ConsoleSession.expires_at > now,
            )
        ).all()
        for active_session in active_sessions:
            active_session.revoked_at = now
        record = ConsoleSession(
            user_id=identity.user_id,
            token_hash=_hash_session_token(token),
            authenticated_at=now,
            expires_at=expires_at,
            created_at=now,
            last_seen_at=now,
        )
        self._db.add(record)
        self._db.commit()
        return LocalConsoleSession(token=token, identity=identity, expires_at=expires_at)

    def authenticate_console_session_state(
        self, token: str
    ) -> LocalAuthenticatedConsoleSession | None:
        token_hash = _hash_session_token(token)
        session = self._db.scalar(
            select(ConsoleSession).where(ConsoleSession.token_hash == token_hash)
        )
        if session is None or session.revoked_at is not None or session.expires_at <= utcnow():
            return None
        user = session.user
        if user is None or not user.is_active:
            return None
        session.last_seen_at = utcnow()
        self._db.commit()
        return LocalAuthenticatedConsoleSession(
            identity=self.build_identity(user),
            authenticated_at=session.authenticated_at,
            expires_at=session.expires_at,
        )

    def authenticate_console_session(self, token: str) -> LocalIdentity | None:
        session_state = self.authenticate_console_session_state(token)
        if session_state is None:
            return None
        return session_state.identity

    def revoke_console_session(self, token: str) -> bool:
        token_hash = _hash_session_token(token)
        session = self._db.scalar(
            select(ConsoleSession).where(ConsoleSession.token_hash == token_hash)
        )
        if session is None or session.revoked_at is not None:
            return False
        session.revoked_at = utcnow()
        self._db.commit()
        return True

    def revoke_user_sessions(self, user_id: object) -> int:
        now = utcnow()
        sessions = self._db.scalars(
            select(ConsoleSession).where(
                ConsoleSession.user_id == user_id,
                ConsoleSession.revoked_at.is_(None),
                ConsoleSession.expires_at > now,
            )
        ).all()
        if not sessions:
            return 0
        for session in sessions:
            session.revoked_at = now
        self._db.commit()
        return len(sessions)

    def get_user_by_username(self, username: str) -> LocalUser | None:
        normalized_username = _normalize_username(username)
        return self._db.scalar(select(LocalUser).where(LocalUser.username == normalized_username))

    def build_identity(self, user: LocalUser) -> LocalIdentity:
        role_names = sorted(link.role.name for link in user.role_links if link.role is not None)
        return LocalIdentity(user_id=user.id, username=user.username, roles=tuple(role_names))
