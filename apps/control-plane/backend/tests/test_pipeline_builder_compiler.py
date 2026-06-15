from __future__ import annotations

import zipfile
from io import BytesIO

import pytest
import yaml
from onestep_control_plane_api.pipeline_builder.compiler import (
    PipelineCompileError,
    PipelineCompiler,
)
from onestep_control_plane_api.pipeline_builder.exporter import WorkerExporter
from onestep_control_plane_api.pipeline_builder.schemas import (
    GraphEdge,
    GraphNode,
    PipelineGraph,
)


def minimal_graph() -> PipelineGraph:
    return PipelineGraph(
        nodes=[
            GraphNode(
                id="source",
                type="interval_source",
                kind="source",
                config={"seconds": 60, "payload": '{"ok": true}'},
                position={"x": 0, "y": 0},
            ),
            GraphNode(
                id="handler",
                type="handler",
                kind="handler",
                mode="visual",
                mapping={"ok": "{{ payload.ok }}"},
                position={"x": 240, "y": 0},
            ),
            GraphNode(
                id="sink",
                type="http_sink",
                kind="sink",
                config={"url": "https://example.invalid/hook"},
                position={"x": 480, "y": 0},
            ),
        ],
        edges=[
            GraphEdge(from_="source", to="handler"),
            GraphEdge(from_="handler", to="sink"),
        ],
    )


def test_compiler_accepts_minimal_source_handler_sink_graph() -> None:
    compiled = PipelineCompiler().compile(minimal_graph(), credentials={})

    assert compiled.order == ["source", "handler", "sink"]
    assert "handler" in compiled.generated_handlers
    assert compiled.required_credentials == []


def test_compiler_rejects_empty_graph() -> None:
    with pytest.raises(PipelineCompileError, match="at least one node"):
        PipelineCompiler().compile(PipelineGraph(), credentials={})


def test_exporter_builds_worker_zip_with_yaml_and_handlers() -> None:
    exported = WorkerExporter().export(
        "pipe_test",
        "Daily Sync",
        minimal_graph(),
        credentials={},
    )

    with zipfile.ZipFile(BytesIO(exported.content)) as archive:
        names = set(archive.namelist())
        assert "daily_sync/worker.yaml" in names
        assert "daily_sync/pyproject.toml" in names
        assert "daily_sync/src/daily_sync/handlers.py" in names
        worker_yaml = yaml.safe_load(archive.read("daily_sync/worker.yaml"))

    assert worker_yaml["apiVersion"] == "onestep/v1alpha1"
    assert worker_yaml["kind"] == "App"
    assert worker_yaml["app"]["name"] == "daily_sync"
    assert {task["name"] for task in worker_yaml["tasks"]} == {"source", "handler", "sink"}
