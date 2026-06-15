from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PipelineStatus = Literal["draft", "valid", "invalid"]
NodeKind = Literal["source", "handler", "sink"]
HandlerMode = Literal["visual", "code"]


class APIModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class GraphNode(APIModel):
    id: str = Field(min_length=1)
    type: str = Field(min_length=1)
    kind: NodeKind | None = None
    credential_ref: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    mode: HandlerMode | None = None
    mapping: dict[str, str] = Field(default_factory=dict)
    code: str | None = None
    input_schema: dict[str, Any] = Field(default_factory=dict)
    position: dict[str, float] = Field(default_factory=dict)

    @field_validator("kind", mode="before")
    @classmethod
    def infer_kind(cls, value: Any, info: Any) -> Any:
        if value:
            return value
        node_type = str(info.data.get("type", ""))
        if node_type == "handler":
            return "handler"
        if node_type.endswith("_sink"):
            return "sink"
        return "source"


class GraphEdge(APIModel):
    from_: str = Field(alias="from", min_length=1)
    to: str = Field(min_length=1)
    condition: str | None = None


class PipelineGraph(APIModel):
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_node_ids(self) -> PipelineGraph:
        ids = [node.id for node in self.nodes]
        if len(ids) != len(set(ids)):
            raise ValueError("graph.nodes ids must be unique")
        return self


class PipelineCreate(APIModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = ""
    graph: PipelineGraph = Field(default_factory=PipelineGraph)


class PipelineUpdate(APIModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    graph: PipelineGraph | None = None


class PipelineRead(APIModel):
    id: str
    name: str
    description: str
    graph: PipelineGraph
    status: PipelineStatus
    created_at: datetime
    updated_at: datetime


class PipelineList(APIModel):
    items: list[PipelineRead]


class ValidationResult(APIModel):
    ok: bool
    message: str


class ExportedWorker(APIModel):
    filename: str


class CredentialCreate(APIModel):
    name: str = Field(min_length=1, max_length=255)
    connector_type: str = Field(min_length=1, max_length=128)
    config: dict[str, Any] = Field(default_factory=dict)
    env_vars: dict[str, str] = Field(default_factory=dict)


class CredentialUpdate(APIModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    connector_type: str | None = Field(default=None, min_length=1, max_length=128)
    config: dict[str, Any] | None = None
    env_vars: dict[str, str] | None = None


class CredentialRead(APIModel):
    id: str
    name: str
    connector_type: str
    config: dict[str, Any]
    env_vars: dict[str, str]
    created_at: datetime
    updated_at: datetime


class CredentialList(APIModel):
    items: list[CredentialRead]


class ConnectorField(APIModel):
    name: str
    label: str
    type: str = "text"
    required: bool = False


class ConnectorDescriptor(APIModel):
    type: str
    label: str
    category: NodeKind
    description: str
    credential_type: str | None = None
    fields: list[ConnectorField] = Field(default_factory=list)


class ConnectorList(APIModel):
    items: list[ConnectorDescriptor]
