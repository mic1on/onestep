from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class InitResult:
    root: Path
    project_name: str
    package_name: str
    files: tuple[Path, ...]
    template: str
    pip_hint: str


def list_templates() -> tuple[str, ...]:
    """Return the available ``onestep init --template`` choices."""
    return tuple(_TEMPLATES)


def init_project(path: str, *, template: str = "interval", force: bool = False) -> InitResult:
    if template not in _TEMPLATES:
        raise ValueError(
            f"unknown template {template!r}; available templates: {', '.join(_TEMPLATES)}"
        )
    root = Path(path).expanduser().resolve()
    project_name, package_name = _derive_names(root)
    file_map = _TEMPLATES[template].files(project_name=project_name, package_name=package_name)

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
        template=template,
        pip_hint=_TEMPLATES[template].pip_hint,
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


@dataclass(frozen=True)
class _Template:
    description: str
    pip_hint: str
    files: Callable[..., dict[Path, str]]


_INTERVAL_HEADER = "apiVersion: onestep/v1alpha1\nkind: App\n"


def _interval_files(*, project_name: str, package_name: str) -> dict[Path, str]:
    return _common_files(project_name=project_name, package_name=package_name)


def _webhook_files(*, project_name: str, package_name: str) -> dict[Path, str]:
    files = _common_files(project_name=project_name, package_name=package_name)
    files[Path("worker.yaml")] = _webhook_yaml(project_name, package_name)
    return files


def _redis_files(*, project_name: str, package_name: str) -> dict[Path, str]:
    files = _common_files(project_name=project_name, package_name=package_name)
    files[Path("worker.yaml")] = _redis_yaml(project_name, package_name)
    return files


def _sql_cdc_files(*, project_name: str, package_name: str) -> dict[Path, str]:
    files = _common_files(project_name=project_name, package_name=package_name)
    files[Path("worker.yaml")] = _sql_cdc_yaml(project_name, package_name)
    return files


def _common_files(*, project_name: str, package_name: str) -> dict[Path, str]:
    return {
        Path("pyproject.toml"): _pyproject_toml(project_name),
        Path("README.md"): _readme_md(project_name),
        Path("worker.yaml"): _interval_yaml(project_name, package_name),
        Path("src") / package_name / "__init__.py": _package_init(project_name),
        Path("src") / package_name / "tasks" / "__init__.py": _tasks_package_init(),
        Path("src") / package_name / "tasks" / "demo.py": _tasks_py(),
        Path("src") / package_name / "transforms" / "__init__.py": _transforms_package_init(),
        Path("src") / package_name / "transforms" / "demo.py": _transforms_py(),
    }


_TEMPLATES: dict[str, _Template] = {
    "interval": _Template(
        description="periodic polling task (interval source, built-in)",
        pip_hint="pip install 'onestep[yaml]'",
        files=_interval_files,
    ),
    "webhook": _Template(
        description="receive webhooks over HTTP (webhook source, built-in)",
        pip_hint="pip install 'onestep[yaml]'",
        files=_webhook_files,
    ),
    "redis": _Template(
        description="consume a Redis Stream with a consumer group",
        pip_hint="pip install 'onestep[redis,yaml]'",
        files=_redis_files,
    ),
    "sql-cdc": _Template(
        description="MySQL binlog CDC into a table sink",
        pip_hint="pip install 'onestep[mysql,yaml]'",
        files=_sql_cdc_files,
    ),
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


def _interval_yaml(project_name: str, package_name: str) -> str:
    return f"""{_INTERVAL_HEADER}
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


def _webhook_yaml(project_name: str, package_name: str) -> str:
    return f"""{_INTERVAL_HEADER}
app:
  name: {project_name}

resources:
  intake:
    type: webhook
    path: /hooks/demo
    methods: [POST]

tasks:
  - name: run_demo
    source: intake
    handler:
      ref: {package_name}.tasks.demo:run_demo
"""


def _redis_yaml(project_name: str, package_name: str) -> str:
    return f"""{_INTERVAL_HEADER}
app:
  name: {project_name}

resources:
  redis:
    type: redis
    url: redis://localhost:6379
  jobs:
    type: redis_stream
    connector: redis
    stream: jobs
    group: workers
    consumer: {package_name}
    create_group: true

tasks:
  - name: run_demo
    source: jobs
    handler:
      ref: {package_name}.tasks.demo:run_demo
"""


def _sql_cdc_yaml(project_name: str, package_name: str) -> str:
    return f"""{_INTERVAL_HEADER}
app:
  name: {project_name}

resources:
  db:
    type: mysql
    dsn: mysql+pymysql://user:password@localhost:3306/app
  cursor:
    type: mysql_cursor_store
    connector: db
  changes:
    type: mysql_binlog
    connector: db
    server_id: 18491
    schemas: [app]
    tables: [events]
    events: [insert, update, delete]
    state: cursor
    state_key: events-cdc
  processed:
    type: mysql_table_sink
    connector: db
    table: processed_events
    mode: upsert
    keys: [id]

tasks:
  - name: run_demo
    source: changes
    emit: [processed]
    handler:
      ref: {package_name}.tasks.demo:run_cdc
"""


def _package_init(project_name: str) -> str:
    return f'"""Application package for {project_name}."""\n'


def _tasks_package_init() -> str:
    return '"""Task handler modules."""\n'


def _tasks_py() -> str:
    return '''from __future__ import annotations

import json
from typing import Any

from ..transforms.demo import normalize_payload


async def run_demo(ctx, payload: dict[str, Any]) -> None:
    row = normalize_payload(payload, app_name=ctx.app.name)
    print(json.dumps(row, ensure_ascii=False))


async def run_cdc(ctx, event: dict[str, Any]) -> dict[str, Any]:
    """Shape a binlog change event into a row for the table sink."""
    row = normalize_payload(event, app_name=ctx.app.name)
    print(json.dumps(row, ensure_ascii=False))
    return event
'''


def _transforms_package_init() -> str:
    return '"""Business transform modules."""\n'


def _transforms_py() -> str:
    return '''from __future__ import annotations

from typing import Any


def normalize_payload(payload: dict[str, Any], *, app_name: str) -> dict[str, Any]:
    return {
        "app": app_name,
        "message": str(payload.get("message") or "hello onestep"),
        "payload": payload,
    }
'''
