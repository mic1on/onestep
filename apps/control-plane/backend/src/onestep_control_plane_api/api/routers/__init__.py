from fastapi import APIRouter

from onestep_control_plane_api.api.routers.agent_ws import router as agent_ws_router
from onestep_control_plane_api.api.routers.auth import router as auth_router
from onestep_control_plane_api.api.routers.commands import router as commands_router
from onestep_control_plane_api.api.routers.health import router as health_router
from onestep_control_plane_api.api.routers.notifications import router as notifications_router
from onestep_control_plane_api.api.routers.pipelines import router as pipelines_router
from onestep_control_plane_api.api.routers.query import router as query_router
from onestep_control_plane_api.api.routers.ui_ws import router as ui_ws_router
from onestep_control_plane_api.api.routers.worker_agent_ws import (
    router as worker_agent_ws_router,
)
from onestep_control_plane_api.api.routers.worker_agents import router as worker_agents_router

router = APIRouter()

router.include_router(auth_router)
router.include_router(health_router)
router.include_router(query_router)
router.include_router(notifications_router)
router.include_router(pipelines_router)
router.include_router(commands_router)
router.include_router(worker_agents_router)
router.include_router(worker_agent_ws_router)
router.include_router(agent_ws_router)
router.include_router(ui_ws_router)
