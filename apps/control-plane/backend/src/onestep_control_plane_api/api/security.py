from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated

from fastapi import (
    Depends,
    HTTPException,
    Request,
    Response,
    Security,
    WebSocket,
    WebSocketException,
    status,
)
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from onestep_control_plane_api.auth.service import LocalAuthService, LocalIdentity, utcnow
from onestep_control_plane_api.core.settings import settings
from onestep_control_plane_api.db.models import LocalUser, WorkerAgent
from onestep_control_plane_api.db.session import get_db_session

AGENT_WS_SUBPROTOCOL = "onestep-agent.v1"

bearer_scheme = HTTPBearer(
    scheme_name="IngestBearerAuth",
    description=(
        "Bearer token used by OneStep reporters to push heartbeat, metrics, events, and sync."
    ),
    auto_error=False,
)
worker_agent_bearer_scheme = HTTPBearer(
    scheme_name="WorkerAgentBearerAuth",
    description="Bearer token used by OneStep worker agents to access host-control APIs.",
    auto_error=False,
)

CONSOLE_AUTH_COOKIE_NAME = "onestep_cp_console_session"
DESTRUCTIVE_COMMAND_KINDS = frozenset({"shutdown", "restart", "drain"})
WRITE_CONSOLE_ROLES = frozenset({"operator", "admin"})
ADMIN_CONSOLE_ROLES = frozenset({"admin"})
CONSOLE_AUTHENTICATED_AT_STATE_KEY = "console_authenticated_at"


@dataclass(frozen=True)
class WebSocketIngestAuth:
    token: str
    accepted_subprotocol: str | None = None


def _validate_ingest_token_value(token: str) -> bool:
    for configured_token in settings.ingest_tokens:
        if secrets.compare_digest(token, configured_token):
            return True
    return False


def _validate_worker_agent_token_value(db: Session, token: str) -> bool:
    token_hash = hash_worker_agent_token(token)
    worker_agent_id = db.scalar(
        select(WorkerAgent.worker_agent_id).where(
            WorkerAgent.connection_token_hash == token_hash
        )
    )
    return worker_agent_id is not None


def hash_worker_agent_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def validate_worker_agent_registration_token_value(token: str) -> bool:
    for configured_token in settings.worker_agent_registration_tokens:
        if secrets.compare_digest(token, configured_token):
            return True
    return False


def _extract_bearer_token(authorization_header: str | None) -> str | None:
    if authorization_header is None:
        return None
    scheme, _, credentials = authorization_header.partition(" ")
    if scheme.lower() != "bearer" or not credentials.strip():
        return None
    return credentials.strip()


def require_ingest_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(bearer_scheme)],
) -> str:
    if not settings.ingest_tokens:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ingestion authentication is not configured",
        )

    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if _validate_ingest_token_value(credentials.credentials):
        return credentials.credentials

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="invalid bearer token",
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_worker_agent_connection(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Security(worker_agent_bearer_scheme),
    ],
    db: Session = Depends(get_db_session),
) -> WorkerAgent:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing worker agent bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token_hash = hash_worker_agent_token(credentials.credentials)
    worker_agent = db.scalar(
        select(WorkerAgent).where(WorkerAgent.connection_token_hash == token_hash)
    )
    if worker_agent is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid worker agent bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return worker_agent


def require_websocket_worker_agent_connection(
    websocket: WebSocket,
    db: Session = Depends(get_db_session),
) -> WorkerAgent:
    token = _extract_bearer_token(websocket.headers.get("authorization"))
    if token is None:
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="missing worker agent bearer token",
        )

    token_hash = hash_worker_agent_token(token)
    worker_agent = db.scalar(
        select(WorkerAgent).where(WorkerAgent.connection_token_hash == token_hash)
    )
    if worker_agent is None:
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="invalid worker agent bearer token",
        )
    return worker_agent


def require_websocket_ingest_token(
    websocket: WebSocket,
    db: Session = Depends(get_db_session),
) -> WebSocketIngestAuth:
    subprotocols = [
        value.strip()
        for value in websocket.headers.get("sec-websocket-protocol", "").split(",")
        if value.strip()
    ]
    accepted_subprotocol = (
        AGENT_WS_SUBPROTOCOL if AGENT_WS_SUBPROTOCOL in subprotocols else None
    )

    token = _extract_bearer_token(websocket.headers.get("authorization"))
    if token is None:
        for value in subprotocols:
            if value.startswith("bearer.") and len(value) > len("bearer."):
                token = value[len("bearer.") :]
                break

    if token is None:
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="missing bearer token",
        )

    if _validate_ingest_token_value(token) or _validate_worker_agent_token_value(db, token):
        return WebSocketIngestAuth(token=token, accepted_subprotocol=accepted_subprotocol)

    raise WebSocketException(
        code=status.WS_1008_POLICY_VIOLATION,
        reason="invalid bearer token",
    )


def is_local_auth_configured(db: Session) -> bool:
    return db.scalar(select(LocalUser.id).limit(1)) is not None


def is_console_auth_enforced(db: Session) -> bool:
    return is_local_auth_configured(db) or settings.console_auth_configured


def should_allow_console_dev_bypass(db: Session) -> bool:
    return (
        settings.app_env == "dev"
        and not is_local_auth_configured(db)
        and not settings.console_auth_configured
    )


def authenticate_console_login(
    db: Session,
    *,
    username: str,
    password: str,
) -> LocalIdentity | None:
    service = LocalAuthService(db)
    if is_local_auth_configured(db):
        return service.authenticate_user(username=username, password=password)

    if (
        settings.app_env == "dev"
        and settings.console_auth_configured
        and secrets.compare_digest(username, settings.console_auth_username)
        and secrets.compare_digest(password, settings.console_auth_password)
    ):
        return LocalIdentity(user_id="dev-shared", username=username, roles=("admin",))

    return None


def get_console_session_identity(request: Request, db: Session) -> LocalIdentity | None:
    if should_allow_console_dev_bypass(db):
        return None

    setattr(request.state, CONSOLE_AUTHENTICATED_AT_STATE_KEY, None)
    cookie_value = request.cookies.get(CONSOLE_AUTH_COOKIE_NAME)
    if not cookie_value:
        return None

    service = LocalAuthService(db)
    session_state = service.authenticate_console_session_state(cookie_value)
    if session_state is not None:
        setattr(
            request.state,
            CONSOLE_AUTHENTICATED_AT_STATE_KEY,
            session_state.authenticated_at,
        )
        return session_state.identity

    if settings.app_env == "dev" and settings.console_auth_configured:
        if secrets.compare_digest(cookie_value, settings.console_auth_username):
            return LocalIdentity(
                user_id="dev-shared",
                username=settings.console_auth_username,
                roles=("admin",),
            )

    return None


def _raise_auth_required() -> None:
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="authentication required",
    )


def require_console_auth(
    request: Request,
    db: Session = Depends(get_db_session),
) -> LocalIdentity | None:
    if should_allow_console_dev_bypass(db):
        return None

    identity = get_console_session_identity(request, db)
    if identity is not None:
        return identity

    _raise_auth_required()


def require_console_identity(
    request: Request,
    db: Session = Depends(get_db_session),
) -> LocalIdentity:
    identity = require_console_auth(request, db)
    if identity is None:
        _raise_auth_required()
    return identity


def require_console_write_access(
    identity: LocalIdentity = Depends(require_console_identity),
) -> LocalIdentity:
    if any(role in WRITE_CONSOLE_ROLES for role in identity.roles):
        return identity
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="insufficient role for command execution",
    )


def require_console_admin(
    identity: LocalIdentity = Depends(require_console_identity),
) -> LocalIdentity:
    if any(role in ADMIN_CONSOLE_ROLES for role in identity.roles):
        return identity
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="admin role required",
    )


def require_command_role(
    command_kind: str,
    identity: LocalIdentity | None,
    request: Request,
) -> LocalIdentity | None:
    if identity is None:
        return None

    if command_kind in DESTRUCTIVE_COMMAND_KINDS:
        if any(role in ADMIN_CONSOLE_ROLES for role in identity.roles):
            authenticated_at = getattr(request.state, CONSOLE_AUTHENTICATED_AT_STATE_KEY, None)
            if authenticated_at is None and identity.user_id != "dev-shared":
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="recent authentication required for destructive commands",
                )
            if authenticated_at is not None and utcnow() - authenticated_at > timedelta(
                seconds=settings.console_sensitive_auth_window_s
            ):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="recent authentication required for destructive commands",
                )
            return identity
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="admin role required for destructive commands",
        )

    if any(role in WRITE_CONSOLE_ROLES for role in identity.roles):
        return identity

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="insufficient role for command execution",
    )


def set_console_auth_cookie(
    response: Response,
    *,
    session_token: str,
) -> None:
    response.set_cookie(
        key=CONSOLE_AUTH_COOKIE_NAME,
        value=session_token,
        max_age=settings.console_auth_session_ttl_s,
        httponly=True,
        samesite="lax",
        secure=settings.app_env == "prod",
        path="/",
    )


def clear_console_auth_cookie(response: Response) -> None:
    response.delete_cookie(
        key=CONSOLE_AUTH_COOKIE_NAME,
        httponly=True,
        samesite="lax",
        secure=settings.app_env == "prod",
        path="/",
    )
