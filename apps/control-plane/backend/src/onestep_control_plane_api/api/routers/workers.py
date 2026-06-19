from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from onestep_control_plane_api.api.schemas import (
    WorkerCreateRequest,
    WorkerDeployRequest,
    WorkerListResponse,
    WorkerSummary,
    WorkerUpdateRequest,
)
from onestep_control_plane_api.api.security import require_console_auth
from onestep_control_plane_api.api.worker_service import (
    _serialize,
    create_worker,
    delete_worker,
    deploy_worker,
    get_worker_or_404,
    list_workers,
    update_worker,
)
from onestep_control_plane_api.db.session import get_db_session

router = APIRouter(prefix="/api/v1", tags=["workers"])


@router.get(
    "/workers",
    response_model=WorkerListResponse,
    dependencies=[Depends(require_console_auth)],
)
def list_workers_endpoint(db: Session = Depends(get_db_session)) -> WorkerListResponse:
    items = list_workers(db)
    return WorkerListResponse(items=items, total=len(items))


@router.post(
    "/workers",
    response_model=WorkerSummary,
    dependencies=[Depends(require_console_auth)],
)
def create_worker_endpoint(
    request: WorkerCreateRequest,
    db: Session = Depends(get_db_session),
) -> WorkerSummary:
    return WorkerSummary(**create_worker(db, request))


@router.get(
    "/workers/{worker_id}",
    response_model=WorkerSummary,
    dependencies=[Depends(require_console_auth)],
)
def get_worker_endpoint(
    worker_id: UUID,
    db: Session = Depends(get_db_session),
) -> WorkerSummary:
    return WorkerSummary(**_serialize(get_worker_or_404(db, worker_id)))


@router.put(
    "/workers/{worker_id}",
    response_model=WorkerSummary,
    dependencies=[Depends(require_console_auth)],
)
def update_worker_endpoint(
    worker_id: UUID,
    request: WorkerUpdateRequest,
    db: Session = Depends(get_db_session),
) -> WorkerSummary:
    return WorkerSummary(**update_worker(db, worker_id, request))


@router.delete(
    "/workers/{worker_id}",
    status_code=204,
    dependencies=[Depends(require_console_auth)],
)
def delete_worker_endpoint(
    worker_id: UUID,
    db: Session = Depends(get_db_session),
) -> None:
    delete_worker(db, worker_id)


@router.post(
    "/workers/{worker_id}/deploy",
    dependencies=[Depends(require_console_auth)],
)
async def deploy_worker_endpoint(
    worker_id: UUID,
    request: WorkerDeployRequest,
    db: Session = Depends(get_db_session),
) -> dict:
    return await deploy_worker(db, worker_id, request)
