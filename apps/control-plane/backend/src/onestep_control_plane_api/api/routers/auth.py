from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from onestep_control_plane_api.api.schemas import ConsoleLoginRequest, ConsoleSessionResponse
from onestep_control_plane_api.api.security import (
    CONSOLE_AUTH_COOKIE_NAME,
    authenticate_console_login,
    clear_console_auth_cookie,
    get_console_session_identity,
    is_console_auth_enforced,
    is_local_auth_configured,
    require_console_identity,
    set_console_auth_cookie,
    should_allow_console_dev_bypass,
)
from onestep_control_plane_api.auth.service import LocalAuthService
from onestep_control_plane_api.core.settings import settings
from onestep_control_plane_api.db.session import get_db_session

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _build_console_session_response(
    *,
    auth_configured: bool,
    bootstrap_required: bool = False,
    authenticated: bool,
    username: str | None = None,
    roles: tuple[str, ...] = (),
) -> ConsoleSessionResponse:
    primary_role = roles[0] if roles else None
    return ConsoleSessionResponse(
        auth_configured=auth_configured,
        bootstrap_required=bootstrap_required,
        authenticated=authenticated,
        username=username,
        role=primary_role,
        roles=list(roles),
    )


@router.get("/session", response_model=ConsoleSessionResponse)
def get_console_session(
    request: Request,
    db: Session = Depends(get_db_session),
) -> ConsoleSessionResponse:
    auth_configured = is_console_auth_enforced(db)
    local_auth_configured = is_local_auth_configured(db)
    if should_allow_console_dev_bypass(db):
        return _build_console_session_response(
            auth_configured=False,
            authenticated=False,
        )

    identity = get_console_session_identity(request, db)
    if identity is None:
        return _build_console_session_response(
            auth_configured=auth_configured,
            bootstrap_required=(
                settings.app_env != "dev"
                and not local_auth_configured
                and not settings.console_auth_configured
            ),
            authenticated=False,
        )

    return _build_console_session_response(
        auth_configured=auth_configured,
        authenticated=True,
        username=identity.username,
        roles=identity.roles,
    )


@router.post("/login", response_model=ConsoleSessionResponse)
def login_console(
    request: ConsoleLoginRequest,
    response: Response,
    db: Session = Depends(get_db_session),
) -> ConsoleSessionResponse:
    if should_allow_console_dev_bypass(db):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="console authentication is not configured",
        )

    if (
        settings.app_env != "dev"
        and not is_local_auth_configured(db)
        and not settings.console_auth_configured
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="local admin bootstrap is required before console login",
        )

    local_auth = LocalAuthService(db)
    if local_auth.is_console_login_locked(request.username):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="invalid username or password",
        )

    identity = authenticate_console_login(
        db,
        username=request.username,
        password=request.password,
    )
    if identity is None:
        local_auth.record_console_login_failure(request.username)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid username or password",
        )

    local_auth.clear_console_login_failures(request.username)
    if identity.user_id == "dev-shared":
        set_console_auth_cookie(response, session_token=identity.username)
        return _build_console_session_response(
            auth_configured=True,
            authenticated=True,
            username=identity.username,
            roles=identity.roles,
        )

    session = local_auth.create_console_session(identity)
    set_console_auth_cookie(response, session_token=session.token)
    return _build_console_session_response(
        auth_configured=True,
        authenticated=True,
        username=identity.username,
        roles=identity.roles,
    )


@router.post("/logout", response_model=ConsoleSessionResponse)
def logout_console(
    request: Request,
    response: Response,
    db: Session = Depends(get_db_session),
) -> ConsoleSessionResponse:
    cookie_value = request.cookies.get(CONSOLE_AUTH_COOKIE_NAME)
    is_dev_shared_cookie = (
        settings.app_env == "dev" and settings.console_auth_username == cookie_value
    )
    if cookie_value and not is_dev_shared_cookie:
        LocalAuthService(db).revoke_console_session(cookie_value)
    clear_console_auth_cookie(response)
    return _build_console_session_response(
        auth_configured=is_console_auth_enforced(db),
        authenticated=False,
    )


@router.post("/logout-all", response_model=ConsoleSessionResponse)
def logout_all_console(
    response: Response,
    identity=Depends(require_console_identity),
    db: Session = Depends(get_db_session),
) -> ConsoleSessionResponse:
    if identity.user_id != "dev-shared":
        LocalAuthService(db).revoke_user_sessions(identity.user_id)
    clear_console_auth_cookie(response)
    return _build_console_session_response(
        auth_configured=is_console_auth_enforced(db),
        authenticated=False,
    )
