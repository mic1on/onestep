from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response, status

from onestep_control_plane_api.api.schemas import ConsoleLoginRequest, ConsoleSessionResponse
from onestep_control_plane_api.api.security import (
    clear_console_auth_cookie,
    get_console_session_username,
    set_console_auth_cookie,
    validate_console_login,
)
from onestep_control_plane_api.core.settings import settings

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.get("/session", response_model=ConsoleSessionResponse)
def get_console_session(request: Request) -> ConsoleSessionResponse:
    if not settings.console_auth_configured:
        return ConsoleSessionResponse(auth_configured=False, authenticated=False)

    username = get_console_session_username(request)
    return ConsoleSessionResponse(
        auth_configured=True,
        authenticated=username is not None,
        username=username,
    )


@router.post("/login", response_model=ConsoleSessionResponse)
def login_console(
    request: ConsoleLoginRequest,
    response: Response,
) -> ConsoleSessionResponse:
    if not settings.console_auth_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="console authentication is not configured",
        )

    if not validate_console_login(request.username, request.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid username or password",
        )

    set_console_auth_cookie(response)
    return ConsoleSessionResponse(
        auth_configured=True,
        authenticated=True,
        username=settings.console_auth_username,
    )


@router.post("/logout", response_model=ConsoleSessionResponse)
def logout_console(response: Response) -> ConsoleSessionResponse:
    clear_console_auth_cookie(response)
    return ConsoleSessionResponse(
        auth_configured=settings.console_auth_configured,
        authenticated=False,
    )
