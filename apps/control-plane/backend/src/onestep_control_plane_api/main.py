import logging
from asyncio import CancelledError, create_task, sleep
from collections.abc import Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import (
    get_redoc_html,
    get_swagger_ui_html,
    get_swagger_ui_oauth2_redirect_html,
)
from fastapi.responses import JSONResponse

from onestep_control_plane_api import __version__
from onestep_control_plane_api.api import router
from onestep_control_plane_api.api.agent_session_service import disconnect_active_sessions
from onestep_control_plane_api.api.notification_service import (
    scan_and_dispatch_missed_start_notifications,
)
from onestep_control_plane_api.api.security import require_console_auth
from onestep_control_plane_api.core import settings
from onestep_control_plane_api.db.session import SessionLocal

logger = logging.getLogger("onestep_control_plane_api.startup")


async def _run_missed_start_scanner(app: FastAPI, *, started_at: datetime) -> None:
    session_factory = getattr(app.state, "session_factory", SessionLocal)
    await sleep(settings.notification_missed_start_scan_interval_s)
    while True:
        try:
            with session_factory() as session:
                scan_and_dispatch_missed_start_notifications(
                    session,
                    min_last_seen_at=started_at,
                )
        except Exception:
            logger.exception("notification missed-start scan failed")
        await sleep(settings.notification_missed_start_scan_interval_s)


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> Iterator[None]:
        session_factory = getattr(app.state, "session_factory", SessionLocal)
        with session_factory() as session:
            disconnected_count = disconnect_active_sessions(session)
        if disconnected_count > 0:
            logger.warning(
                "marked stale active agent sessions as disconnected on startup",
                extra={"disconnected_session_count": disconnected_count},
            )
        scanner_task = create_task(
            _run_missed_start_scanner(app, started_at=datetime.now(UTC))
        )
        yield
        scanner_task.cancel()
        try:
            await scanner_task
        except CancelledError:
            pass

    app = FastAPI(
        title="OneStep Control Plane API",
        version=__version__,
        debug=settings.debug,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.session_factory = SessionLocal
    if settings.cors_allow_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_allow_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    app.include_router(router)

    @app.get("/openapi.json", include_in_schema=False)
    def openapi_schema(_: str | None = Depends(require_console_auth)) -> JSONResponse:
        return JSONResponse(app.openapi())

    @app.get("/docs", include_in_schema=False)
    def swagger_ui(_: str | None = Depends(require_console_auth)):
        return get_swagger_ui_html(
            openapi_url="/openapi.json",
            title=f"{app.title} - Swagger UI",
            oauth2_redirect_url="/docs/oauth2-redirect",
        )

    @app.get("/docs/oauth2-redirect", include_in_schema=False)
    def swagger_ui_redirect(_: str | None = Depends(require_console_auth)):
        return get_swagger_ui_oauth2_redirect_html()

    @app.get("/redoc", include_in_schema=False)
    def redoc_ui(_: str | None = Depends(require_console_auth)):
        return get_redoc_html(
            openapi_url="/openapi.json",
            title=f"{app.title} - ReDoc",
        )

    return app


app = create_app()
