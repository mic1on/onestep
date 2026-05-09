from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class InitResult:
    root: Path
    project_name: str
    package_name: str
    files: tuple[Path, ...]


def init_project(path: str, *, force: bool = False) -> InitResult:
    root = Path(path).expanduser().resolve()
    project_name, package_name = _derive_names(root)
    file_map = _build_file_map(project_name=project_name, package_name=package_name)

    conflicts = tuple(root / relative_path for relative_path in file_map if (root / relative_path).exists())
    if conflicts and not force:
        raise FileExistsError(
            "refusing to overwrite existing files: " + ", ".join(str(path.relative_to(root)) for path in conflicts)
        )

    for relative_path, content in file_map.items():
        destination = root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")

    return InitResult(
        root=root,
        project_name=project_name,
        package_name=package_name,
        files=tuple(root / relative_path for relative_path in file_map),
    )


def _derive_names(root: Path) -> tuple[str, str]:
    raw_name = root.name.strip() or "onestep-worker"
    project_name = _normalize_project_name(raw_name)
    package_name = _normalize_package_name(raw_name)
    return project_name, package_name


def _normalize_project_name(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    if not normalized:
        return "onestep-worker"
    if normalized[0].isdigit():
        return f"worker-{normalized}"
    return normalized


def _normalize_package_name(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_").lower()
    if not normalized:
        return "onestep_worker"
    if normalized[0].isdigit():
        return f"worker_{normalized}"
    return normalized


def _build_file_map(*, project_name: str, package_name: str) -> dict[Path, str]:
    return {
        Path("pyproject.toml"): _pyproject_toml(project_name),
        Path("README.md"): _readme_md(project_name),
        Path("worker.yaml"): _worker_yaml(project_name, package_name),
        Path("src") / package_name / "__init__.py": _package_init(project_name),
        Path("src") / package_name / "tasks" / "__init__.py": _tasks_package_init(),
        Path("src") / package_name / "tasks" / "demo.py": _tasks_py(),
        Path("src") / package_name / "transforms" / "__init__.py": _transforms_package_init(),
        Path("src") / package_name / "transforms" / "demo.py": _transforms_py(),
    }


def _pyproject_toml(project_name: str) -> str:
    return f"""[project]
name = "{project_name}"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = [
    "onestep[yaml]",
]
"""


def _readme_md(project_name: str) -> str:
    return f"""# {project_name}

This project was scaffolded by `onestep init`.

Files:

- `worker.yaml`: runtime wiring
- `src/.../tasks/`: task handlers
- `src/.../transforms/`: business transforms

Common commands:

```bash
onestep check --strict worker.yaml
onestep run worker.yaml
```

Add more tasks by:

1. creating a new module under `src/.../tasks/`
2. adding any shared transform logic under `src/.../transforms/`
3. appending a new entry under `tasks:` in `worker.yaml`

Add `src/.../hooks.py` only when you really need lifecycle or task hooks, and then
wire those hooks explicitly in `worker.yaml`.
"""


def _worker_yaml(project_name: str, package_name: str) -> str:
    return f"""apiVersion: onestep/v1alpha1
kind: App

app:
  name: {project_name}

resources:
  tick:
    type: interval
    seconds: 60
    immediate: true
    payload:
      message: hello onestep

tasks:
  - name: run_demo
    source: tick
    handler:
      ref: {package_name}.tasks.demo:run_demo
"""


def _package_init(project_name: str) -> str:
    return f'"""Application package for {project_name}."""\n'


def _tasks_package_init() -> str:
    return '"""Task handler modules."""\n'


def _tasks_py() -> str:
    return """from __future__ import annotations

import json
from typing import Any

from ..transforms.demo import normalize_payload


async def run_demo(ctx, payload: dict[str, Any]) -> None:
    row = normalize_payload(payload, app_name=ctx.app.name)
    print(json.dumps(row, ensure_ascii=False))
"""


def _transforms_package_init() -> str:
    return '"""Business transform modules."""\n'


def _transforms_py() -> str:
    return """from __future__ import annotations

from typing import Any


def normalize_payload(payload: dict[str, Any], *, app_name: str) -> dict[str, Any]:
    return {
        "app": app_name,
        "message": str(payload.get("message") or "hello onestep"),
        "payload": payload,
    }
"""
