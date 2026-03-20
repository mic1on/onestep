from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from typing import Annotated

from fastapi import HTTPException, Request, Response, Security, WebSocket, WebSocketException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from onestep_control_plane_api.core.settings import settings

AGENT_WS_SUBPROTOCOL = "onestep-agent.v1"

bearer_scheme = HTTPBearer(
    scheme_name="IngestBearerAuth",
    description=(
        "Bearer token used by OneStep reporters to push heartbeat, metrics, events, and sync."
    ),
    auto_error=False,
)

CONSOLE_AUTH_COOKIE_NAME = "onestep_cp_console_session"


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


def validate_console_login(username: str, password: str) -> bool:
    if not settings.console_auth_configured:
        return False
    return secrets.compare_digest(username, settings.console_auth_username) and secrets.compare_digest(
        password,
        settings.console_auth_password,
    )


def _build_console_session_signature(username: str, expires_at: int) -> str:
    message = f"{username}:{expires_at}".encode()
    secret = settings.console_auth_password.encode()
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def build_console_session_cookie_value() -> str:
    expires_at = int(time.time()) + settings.console_auth_session_ttl_s
    signature = _build_console_session_signature(settings.console_auth_username, expires_at)
    return f"{settings.console_auth_username}:{expires_at}:{signature}"


def get_console_session_username(request: Request) -> str | None:
    if not settings.console_auth_configured:
        return None

    cookie_value = request.cookies.get(CONSOLE_AUTH_COOKIE_NAME)
    if not cookie_value:
        return None

    username, separator, remainder = cookie_value.partition(":")
    expires_at_raw, separator_two, signature = remainder.partition(":")
    if not separator or not separator_two:
        return None

    if not secrets.compare_digest(username, settings.console_auth_username):
        return None

    try:
        expires_at = int(expires_at_raw)
    except ValueError:
        return None

    if expires_at < int(time.time()):
        return None

    expected_signature = _build_console_session_signature(username, expires_at)
    if not secrets.compare_digest(signature, expected_signature):
        return None

    return username


def require_console_auth(request: Request) -> str | None:
    if not settings.console_auth_configured:
        return None

    username = get_console_session_username(request)
    if username is not None:
        return username

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="authentication required",
    )


def set_console_auth_cookie(response: Response) -> None:
    response.set_cookie(
        key=CONSOLE_AUTH_COOKIE_NAME,
        value=build_console_session_cookie_value(),
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
