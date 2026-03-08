from fastapi import APIRouter

from onestep_control_plane_api.api.routers.health import router as health_router
from onestep_control_plane_api.api.routers.ingestion import router as ingestion_router
from onestep_control_plane_api.api.routers.query import router as query_router

router = APIRouter()

router.include_router(health_router)
router.include_router(query_router)
router.include_router(ingestion_router)
