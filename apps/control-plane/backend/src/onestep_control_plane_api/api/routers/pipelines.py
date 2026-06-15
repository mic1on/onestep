from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from onestep_control_plane_api.api.security import require_console_auth
from onestep_control_plane_api.db.models import Pipeline
from onestep_control_plane_api.db.session import get_db_session
from onestep_control_plane_api.pipeline_builder.compiler import (
    PipelineCompileError,
    PipelineCompiler,
)
from onestep_control_plane_api.pipeline_builder.connectors import CONNECTORS
from onestep_control_plane_api.pipeline_builder.schemas import (
    ConnectorList,
    PipelineCreate,
    PipelineGraph,
    PipelineList,
    PipelineRead,
    PipelineUpdate,
    ValidationResult,
)

router = APIRouter(
    prefix="/api/v1",
    tags=["pipelines"],
    dependencies=[Depends(require_console_auth)],
)


@router.get("/connectors", response_model=ConnectorList)
def list_connectors() -> ConnectorList:
    return ConnectorList(items=CONNECTORS)


@router.get("/pipelines", response_model=PipelineList)
def list_pipelines(db: Session = Depends(get_db_session)) -> PipelineList:
    rows = db.scalars(select(Pipeline).order_by(Pipeline.updated_at.desc())).all()
    return PipelineList(items=[_pipeline_read(row) for row in rows])


@router.post("/pipelines", response_model=PipelineRead)
def create_pipeline(
    request: PipelineCreate,
    db: Session = Depends(get_db_session),
) -> PipelineRead:
    pipeline = Pipeline(
        name=request.name,
        description=request.description,
        graph_json=request.graph.model_dump(by_alias=True),
        status="draft",
    )
    db.add(pipeline)
    db.commit()
    db.refresh(pipeline)
    return _pipeline_read(pipeline)


@router.get("/pipelines/{pipeline_id}", response_model=PipelineRead)
def get_pipeline(pipeline_id: UUID, db: Session = Depends(get_db_session)) -> PipelineRead:
    return _pipeline_read(_get_pipeline_or_404(db, pipeline_id))


@router.put("/pipelines/{pipeline_id}", response_model=PipelineRead)
def update_pipeline(
    pipeline_id: UUID,
    request: PipelineUpdate,
    db: Session = Depends(get_db_session),
) -> PipelineRead:
    pipeline = _get_pipeline_or_404(db, pipeline_id)
    changed = False

    if request.name is not None:
        pipeline.name = request.name
        changed = True
    if request.description is not None:
        pipeline.description = request.description
        changed = True
    if request.graph is not None:
        pipeline.graph_json = request.graph.model_dump(by_alias=True)
        changed = True
    if changed:
        pipeline.status = "draft"

    db.commit()
    db.refresh(pipeline)
    return _pipeline_read(pipeline)


@router.delete("/pipelines/{pipeline_id}", status_code=204)
def delete_pipeline(pipeline_id: UUID, db: Session = Depends(get_db_session)) -> Response:
    pipeline = _get_pipeline_or_404(db, pipeline_id)
    db.delete(pipeline)
    db.commit()
    return Response(status_code=204)


@router.post("/pipelines/{pipeline_id}/validate", response_model=ValidationResult)
def validate_pipeline(
    pipeline_id: UUID,
    db: Session = Depends(get_db_session),
) -> ValidationResult:
    pipeline = _get_pipeline_or_404(db, pipeline_id)
    graph = PipelineGraph.model_validate(pipeline.graph_json)
    try:
        PipelineCompiler().compile(graph, credentials={})
    except PipelineCompileError as exc:
        pipeline.status = "invalid"
        db.commit()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    pipeline.status = "valid"
    db.commit()
    return ValidationResult(ok=True, message="pipeline is valid")


def _get_pipeline_or_404(db: Session, pipeline_id: UUID) -> Pipeline:
    pipeline = db.get(Pipeline, pipeline_id)
    if pipeline is None:
        raise HTTPException(status_code=404, detail="pipeline not found")
    return pipeline


def _pipeline_read(pipeline: Pipeline) -> PipelineRead:
    return PipelineRead(
        id=str(pipeline.id),
        name=pipeline.name,
        description=pipeline.description,
        graph=PipelineGraph.model_validate(pipeline.graph_json),
        status=pipeline.status,
        created_at=pipeline.created_at,
        updated_at=pipeline.updated_at,
    )
