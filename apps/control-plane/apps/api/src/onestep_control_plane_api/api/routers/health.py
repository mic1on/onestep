from fastapi import APIRouter

from onestep_control_plane_api.core.settings import settings

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz() -> dict[str, str | bool]:
    return {
        "status": "ok",
        "ingestion_auth_configured": settings.ingest_auth_configured,
    }


@router.get("/readyz")
def readyz() -> dict[str, str | bool]:
    return {
        "status": "ready",
        "environment": settings.app_env,
        "ingestion_auth_configured": settings.ingest_auth_configured,
    }
