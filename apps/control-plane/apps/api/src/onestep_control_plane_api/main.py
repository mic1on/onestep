from fastapi import FastAPI

from onestep_control_plane_api import __version__
from onestep_control_plane_api.api import router
from onestep_control_plane_api.core import settings


def create_app() -> FastAPI:
    app = FastAPI(
        title="OneStep Control Plane API",
        version=__version__,
        debug=settings.debug,
    )
    app.include_router(router)
    return app


app = create_app()
