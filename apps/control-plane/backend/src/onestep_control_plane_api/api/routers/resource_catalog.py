from __future__ import annotations

from fastapi import APIRouter, Depends

from onestep_control_plane_api.api.resource_catalog import catalog_response
from onestep_control_plane_api.api.schemas import ResourceCatalogResponse
from onestep_control_plane_api.api.security import require_console_auth

router = APIRouter(prefix="/api/v1", tags=["resource-catalog"])


@router.get(
    "/resource-catalog",
    response_model=ResourceCatalogResponse,
    response_model_exclude_none=True,
    dependencies=[Depends(require_console_auth)],
)
def get_resource_catalog_endpoint() -> ResourceCatalogResponse:
    return ResourceCatalogResponse(**catalog_response())
