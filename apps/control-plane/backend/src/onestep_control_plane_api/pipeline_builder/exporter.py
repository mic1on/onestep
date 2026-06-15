from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version

import yaml

from onestep_control_plane_api.pipeline_builder.compiler import PipelineCompiler
from onestep_control_plane_api.pipeline_builder.onestep_adapter import (
    build_env_example,
    build_onestep_config,
    build_requirements,
)
from onestep_control_plane_api.pipeline_builder.schemas import PipelineGraph


@dataclass(frozen=True)
class ExportedWorker:
    filename: str
    content: bytes


class WorkerExporter:
    def __init__(self, compiler: PipelineCompiler | None = None) -> None:
        self.compiler = compiler or PipelineCompiler()

    def export(
        self,
        pipeline_id: str,
        pipeline_name: str,
        graph: PipelineGraph,
        *,
        credentials: dict[str, dict] | None = None,
    ) -> ExportedWorker:
        active_credentials = credentials or self._credential_refs(graph)
        compiled = self.compiler.compile(graph, credentials=active_credentials)
        package_name = _slugify(pipeline_name)
        config = build_onestep_config(
            pipeline_name,
            graph,
            handler_module=f"{package_name}.handlers",
            credentials=active_credentials,
            runtime=False,
            compiler=self.compiler,
        )
        worker_yaml = yaml.safe_dump(config, allow_unicode=True, sort_keys=False)
        handlers_py = self._build_handlers(
            package_name,
            compiled.generated_handlers,
            compiled.generated_predicates,
        )
        env_example = build_env_example(config)
        requirements = "\n".join(build_requirements(graph)) + "\n"

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            root = f"{package_name}/"
            archive.writestr(root + "pyproject.toml", _worker_pyproject(package_name))
            archive.writestr(root + "worker.yaml", worker_yaml)
            archive.writestr(root + "docker-compose.yml", _worker_compose())
            archive.writestr(root + ".env.example", env_example)
            archive.writestr(root + "requirements.txt", requirements)
            archive.writestr(root + f"src/{package_name}/__init__.py", "")
            archive.writestr(root + f"src/{package_name}/handlers.py", handlers_py)
        return ExportedWorker(filename=f"{pipeline_id}.zip", content=buffer.getvalue())

    def _build_handlers(
        self,
        package_name: str,
        handlers: dict[str, str],
        predicates: dict[str, str],
    ) -> str:
        lines = ["from __future__ import annotations", ""]
        for node_id, code in handlers.items():
            handler_name = _handler_name(node_id)
            code = re.sub(r"async def\s+handler\s*\(", f"async def {handler_name}(", code, count=1)
            lines.append(code.rstrip())
            lines.append("")
        for code in predicates.values():
            lines.append(code.rstrip())
            lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _credential_refs(graph: PipelineGraph) -> dict[str, dict]:
        return {node.credential_ref: {} for node in graph.nodes if node.credential_ref}


def _handler_name(node_id: str) -> str:
    return f"handler_{re.sub(r'[^0-9A-Za-z_]', '_', node_id)}"


def _slugify(value: str) -> str:
    slug = re.sub(r"[^0-9A-Za-z_]+", "_", value.strip().lower()).strip("_")
    return slug or "onestep_worker"


def _worker_pyproject(package_name: str) -> str:
    return f"""[project]
name = "{package_name}"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["onestep[yaml]"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
"""


def _worker_compose() -> str:
    return f"""services:
  worker:
    image: "${{ONESTEP_WORKER_IMAGE:-{_default_worker_image()}}}"
    working_dir: /workspace
    volumes:
      - ./:/workspace
    env_file:
      - .env.example
    environment:
      ONESTEP_TARGET: /workspace/worker.yaml
"""


def _default_worker_image() -> str:
    try:
        tag = version("onestep")
    except PackageNotFoundError:
        tag = "latest"
    tag = re.sub(r"[^0-9A-Za-z_.-]", "-", tag)
    return f"ghcr.io/mic1on/onestep-worker:{tag}"
