import logging
from asyncio import CancelledError, create_task
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
from onestep_control_plane_api.api.security import require_console_auth
from onestep_control_plane_api.core import settings
from onestep_control_plane_api.db.session import SessionLocal
from onestep_control_plane_api.ops.readiness import (
    build_default_background_task_states,
)
from onestep_control_plane_api.workers.notification_scanner import (
    NOTIFICATION_MISSED_START_SCANNER_NAME,
    run_notification_missed_start_scanner,
)
from onestep_control_plane_api.workers.retention_worker import (
    RETENTION_WORKER_NAME,
    run_retention_worker,
)

logger = logging.getLogger("onestep_control_plane_api.startup")


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
            run_notification_missed_start_scanner(
                app,
                started_at=datetime.now(UTC),
            )
        )
        retention_task = create_task(
            run_retention_worker(
                app,
                started_at=datetime.now(UTC),
            )
        )
        app.state.background_task_refs[NOTIFICATION_MISSED_START_SCANNER_NAME] = scanner_task
        app.state.background_task_refs[RETENTION_WORKER_NAME] = retention_task
        yield
        for name, task in (
            (NOTIFICATION_MISSED_START_SCANNER_NAME, scanner_task),
            (RETENTION_WORKER_NAME, retention_task),
        ):
            task.cancel()
            try:
                await task
            except CancelledError:
                pass
            finally:
                app.state.background_task_refs[name] = None

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
    app.state.background_task_states = build_default_background_task_states()
    app.state.background_task_refs = {
        NOTIFICATION_MISSED_START_SCANNER_NAME: None,
        RETENTION_WORKER_NAME: None,
    }
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
