from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from onestep_control_plane_api.api.connector_service import (
    build_connector_summary,
    create_connector,
    delete_connector,
    get_connector_or_404,
    list_connectors,
    split_payload,
    update_connector,
)
from onestep_control_plane_api.api.schemas import (
    ConnectorCreateRequest,
    ConnectorListResponse,
    ConnectorSummary,
    ConnectorUpdateRequest,
)
from onestep_control_plane_api.api.security import require_console_auth
from onestep_control_plane_api.db.session import get_db_session

router = APIRouter(prefix="/api/v1", tags=["connectors"])


@router.get(
    "/connectors",
    response_model=ConnectorListResponse,
    dependencies=[Depends(require_console_auth)],
)
def list_connectors_endpoint(
    type: Annotated[str | None, Query()] = None,
    db: Session = Depends(get_db_session),
) -> ConnectorListResponse:
    items = list_connectors(db, type_filter=type)
    return ConnectorListResponse(items=items, total=len(items))


@router.post(
    "/connectors",
    response_model=ConnectorSummary,
    dependencies=[Depends(require_console_auth)],
)
def create_connector_endpoint(
    request: ConnectorCreateRequest,
    db: Session = Depends(get_db_session),
) -> ConnectorSummary:
    config, secret = split_payload(
        request.type, {**request.config, **request.secret}
    )
    summary = create_connector(
        db, name=request.name, connector_type=request.type, config=config, secret=secret
    )
    return ConnectorSummary(**summary)


@router.get(
    "/connectors/{connector_id}",
    response_model=ConnectorSummary,
    dependencies=[Depends(require_console_auth)],
)
def get_connector_endpoint(
    connector_id: UUID,
    db: Session = Depends(get_db_session),
) -> ConnectorSummary:
    connector = get_connector_or_404(db, connector_id)
    return ConnectorSummary(**build_connector_summary(connector))


@router.put(
    "/connectors/{connector_id}",
    response_model=ConnectorSummary,
    dependencies=[Depends(require_console_auth)],
)
def update_connector_endpoint(
    connector_id: UUID,
    request: ConnectorUpdateRequest,
    db: Session = Depends(get_db_session),
) -> ConnectorSummary:
    summary = update_connector(
        db,
        connector_id,
        name=request.name,
        config=request.config,
        secret=request.secret,
    )
    return ConnectorSummary(**summary)


@router.delete(
    "/connectors/{connector_id}",
    status_code=204,
    dependencies=[Depends(require_console_auth)],
)
def delete_connector_endpoint(
    connector_id: UUID,
    db: Session = Depends(get_db_session),
) -> None:
    delete_connector(db, connector_id)
