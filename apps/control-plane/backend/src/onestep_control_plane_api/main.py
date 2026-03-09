from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from onestep_control_plane_api import __version__
from onestep_control_plane_api.api import router
from onestep_control_plane_api.core import settings


def create_app() -> FastAPI:
    app = FastAPI(
        title="OneStep Control Plane API",
        version=__version__,
        debug=settings.debug,
    )
    if settings.cors_allow_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_allow_origins,
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    app.include_router(router)
    return app


app = create_app()
