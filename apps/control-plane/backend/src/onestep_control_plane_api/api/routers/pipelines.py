from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from onestep_control_plane_api.api.security import require_console_auth
from onestep_control_plane_api.db.models import Pipeline, PipelineCredential
from onestep_control_plane_api.db.session import get_db_session
from onestep_control_plane_api.pipeline_builder.compiler import (
    PipelineCompileError,
    PipelineCompiler,
)
from onestep_control_plane_api.pipeline_builder.connectors import CONNECTORS
from onestep_control_plane_api.pipeline_builder.credentials import (
    CredentialCipher,
    load_cipher,
    mask_env_vars,
    merge_masked_env_vars,
)
from onestep_control_plane_api.pipeline_builder.exporter import WorkerExporter
from onestep_control_plane_api.pipeline_builder.schemas import (
    ConnectorList,
    CredentialCreate,
    CredentialList,
    CredentialRead,
    CredentialUpdate,
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
        PipelineCompiler().compile(graph, credentials=_credential_map(db))
    except PipelineCompileError as exc:
        pipeline.status = "invalid"
        db.commit()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    pipeline.status = "valid"
    db.commit()
    return ValidationResult(ok=True, message="pipeline is valid")


@router.get("/pipeline-credentials", response_model=CredentialList)
def list_pipeline_credentials(db: Session = Depends(get_db_session)) -> CredentialList:
    cipher = load_cipher()
    rows = db.scalars(
        select(PipelineCredential).order_by(PipelineCredential.name.asc())
    ).all()
    return CredentialList(items=[_credential_read(row, cipher, masked=True) for row in rows])


@router.post("/pipeline-credentials", response_model=CredentialRead)
def create_pipeline_credential(
    request: CredentialCreate,
    db: Session = Depends(get_db_session),
) -> CredentialRead:
    cipher = load_cipher()
    credential = PipelineCredential(
        name=request.name,
        connector_type=request.connector_type,
        config_encrypted=cipher.encrypt_json(request.config),
        env_vars_encrypted=cipher.encrypt_json(request.env_vars),
    )
    db.add(credential)
    db.commit()
    db.refresh(credential)
    return _credential_read(credential, cipher, masked=True)


@router.put("/pipeline-credentials/{credential_id}", response_model=CredentialRead)
def update_pipeline_credential(
    credential_id: UUID,
    request: CredentialUpdate,
    db: Session = Depends(get_db_session),
) -> CredentialRead:
    cipher = load_cipher()
    credential = _get_credential_or_404(db, credential_id)
    if request.name is not None:
        credential.name = request.name
    if request.connector_type is not None:
        credential.connector_type = request.connector_type
    if request.config is not None:
        credential.config_encrypted = cipher.encrypt_json(request.config)
    if request.env_vars is not None:
        existing_env_vars = cipher.decrypt_json(credential.env_vars_encrypted)
        credential.env_vars_encrypted = cipher.encrypt_json(
            merge_masked_env_vars(request.env_vars, existing_env_vars)
        )
    db.commit()
    db.refresh(credential)
    return _credential_read(credential, cipher, masked=True)


@router.delete("/pipeline-credentials/{credential_id}", status_code=204)
def delete_pipeline_credential(
    credential_id: UUID,
    db: Session = Depends(get_db_session),
) -> Response:
    credential = _get_credential_or_404(db, credential_id)
    db.delete(credential)
    db.commit()
    return Response(status_code=204)


@router.post("/pipelines/{pipeline_id}/export")
def export_pipeline(pipeline_id: UUID, db: Session = Depends(get_db_session)) -> Response:
    pipeline = _get_pipeline_or_404(db, pipeline_id)
    graph = PipelineGraph.model_validate(pipeline.graph_json)
    try:
        exported = WorkerExporter().export(
            str(pipeline.id),
            pipeline.name,
            graph,
            credentials=_credential_map(db),
        )
    except PipelineCompileError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return Response(
        content=exported.content,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{exported.filename}"'},
    )


def _get_pipeline_or_404(db: Session, pipeline_id: UUID) -> Pipeline:
    pipeline = db.get(Pipeline, pipeline_id)
    if pipeline is None:
        raise HTTPException(status_code=404, detail="pipeline not found")
    return pipeline


def _get_credential_or_404(db: Session, credential_id: UUID) -> PipelineCredential:
    credential = db.get(PipelineCredential, credential_id)
    if credential is None:
        raise HTTPException(status_code=404, detail="pipeline credential not found")
    return credential


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


def _credential_read(
    credential: PipelineCredential,
    cipher: CredentialCipher,
    *,
    masked: bool,
) -> CredentialRead:
    env_vars = cipher.decrypt_json(credential.env_vars_encrypted)
    readable_env_vars = (
        mask_env_vars(env_vars)
        if masked
        else {key: str(value) for key, value in env_vars.items()}
    )
    return CredentialRead(
        id=str(credential.id),
        name=credential.name,
        connector_type=credential.connector_type,
        config=cipher.decrypt_json(credential.config_encrypted),
        env_vars=readable_env_vars,
        created_at=credential.created_at,
        updated_at=credential.updated_at,
    )


def _credential_map(db: Session) -> dict[str, dict[str, object]]:
    cipher = load_cipher()
    credentials = db.scalars(select(PipelineCredential)).all()
    return {
        str(credential.id): {
            **cipher.decrypt_json(credential.config_encrypted),
            "env": cipher.decrypt_json(credential.env_vars_encrypted),
        }
        for credential in credentials
    }
