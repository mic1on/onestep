from __future__ import annotations

import secrets
from dataclasses import dataclass
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

from onestep_control_plane_api.auth.service import LocalAuthService, LocalIdentity
from onestep_control_plane_api.core.settings import settings
from onestep_control_plane_api.db.models import LocalUser
from onestep_control_plane_api.db.session import get_db_session

AGENT_WS_SUBPROTOCOL = "onestep-agent.v1"

bearer_scheme = HTTPBearer(
    scheme_name="IngestBearerAuth",
    description=(
        "Bearer token used by OneStep reporters to push heartbeat, metrics, events, and sync."
    ),
    auto_error=False,
)

CONSOLE_AUTH_COOKIE_NAME = "onestep_cp_console_session"
DESTRUCTIVE_COMMAND_KINDS = frozenset({"shutdown", "restart", "drain"})
WRITE_CONSOLE_ROLES = frozenset({"operator", "admin"})
ADMIN_CONSOLE_ROLES = frozenset({"admin"})


@dataclass(frozen=True)
class WebSocketIngestAuth:
    token: str
    accepted_subprotocol: str | None = None


def _validate_ingest_token_value(token: str) -> bool:
    for configured_token in settings.ingest_tokens:
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


def require_websocket_ingest_token(websocket: WebSocket) -> WebSocketIngestAuth:
    if not settings.ingest_tokens:
        raise WebSocketException(
            code=status.WS_1011_INTERNAL_ERROR,
            reason="ingestion authentication is not configured",
        )

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

    if not _validate_ingest_token_value(token):
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="invalid bearer token",
        )

    return WebSocketIngestAuth(token=token, accepted_subprotocol=accepted_subprotocol)


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

    cookie_value = request.cookies.get(CONSOLE_AUTH_COOKIE_NAME)
    if not cookie_value:
        return None

    service = LocalAuthService(db)
    identity = service.authenticate_console_session(cookie_value)
    if identity is not None:
        return identity

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
) -> LocalIdentity | None:
    if identity is None:
        return None

    if command_kind in DESTRUCTIVE_COMMAND_KINDS:
        if any(role in ADMIN_CONSOLE_ROLES for role in identity.roles):
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
