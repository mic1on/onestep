from fastapi import APIRouter, Request, Response, status

from onestep_control_plane_api.core.settings import settings
from onestep_control_plane_api.ops.readiness import build_readiness_report

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz() -> dict[str, str | bool]:
    return {
        "status": "ok",
        "ingestion_auth_configured": settings.ingest_auth_configured,
    }


@router.get("/readyz")
def readyz(request: Request, response: Response) -> dict[str, object]:
    report = build_readiness_report(request.app)
    if not report.ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return report.to_response()
