# onestep-control-plane Plugin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move onestep control-plane reporter and WebSocket runtime integration into a separate `onestep-control-plane` plugin while preserving source-compatible imports for users who install `onestep[control-plane]`.

**Architecture:** Keep task control primitives, identity helpers, custom metrics, and runtime snapshots in core. Add a small core reporter registry using the same entry-point pattern as resource plugins, then put `ControlPlaneReporter`, `ControlPlaneReporterConfig`, `ControlPlaneWsSender`, and `ControlPlaneWsTransport` in `plugins/onestep-control-plane`. Core `onestep.reporter`, `onestep.control_plane_ws`, and top-level `onestep` exports become lazy compatibility shims that raise a clear install message when the plugin is unavailable.

**Tech Stack:** Python `>=3.9`, onestep reporter/resource registry patterns, Python entry points, `websockets>=12.0`, pytest, uv workspace packages, hatchling.

---

## File Structure

- Create `plugins/onestep-control-plane/pyproject.toml`: package metadata, `websockets` dependency, and `onestep.reporters` entry point.
- Create `plugins/onestep-control-plane/README.md`: install, YAML, Python import, and compatibility notes.
- Create `plugins/onestep-control-plane/src/onestep_control_plane/__init__.py`: public exports and `register` entry point target.
- Create `plugins/onestep-control-plane/src/onestep_control_plane/reporter.py`: moved implementation from `src/onestep/reporter.py`, adjusted to import core runtime types from `onestep`.
- Create `plugins/onestep-control-plane/src/onestep_control_plane/ws.py`: moved implementation from `src/onestep/control_plane_ws.py`, adjusted to import reporter config from the plugin module.
- Create `plugins/onestep-control-plane/tests/`: moved control-plane reporter, WebSocket, reconnect, heartbeat, runtime identity, replica identity, sync reporter, runtime descriptor, and plugin registration tests.
- Create `src/onestep/reporter_registry.py`: core reporter plugin registry and helper install error.
- Replace `src/onestep/reporter.py`: lazy shim for `ControlPlaneReporter` and `ControlPlaneReporterConfig`.
- Replace `src/onestep/control_plane_ws.py`: lazy shim for WebSocket transport/sender helpers.
- Modify `src/onestep/config.py`: attach YAML reporters through `reporter_registry` instead of direct imports.
- Modify `src/onestep/__init__.py`: remove eager control-plane imports and add lazy `__getattr__` compatibility.
- Modify `pyproject.toml`: bump core to `1.5.0`, add `onestep-control-plane` workspace member/source, and make the `control-plane` extra depend on the plugin.
- Modify `scripts/run-reliability-checks.sh`: include `plugins/onestep-control-plane/tests`.
- Modify `tests/test_packaging.py` and `tests/test_cli.py`: update core-only packaging and YAML reporter tests around the registry.
- Modify `README.md`, `example/README.md`, `CHANGELOG.md`, and `uv.lock`: document the split and refresh lock metadata.

## Compatibility Decisions

- Base `pip install onestep` must not import `websockets` and must not require `onestep-control-plane`.
- `pip install 'onestep[control-plane]'` installs `onestep-control-plane>=0.1.0`; then these continue working:

```python
from onestep import ControlPlaneReporter, ControlPlaneReporterConfig
from onestep.control_plane_ws import ControlPlaneWsSender, build_control_plane_ws_url
```

- YAML keeps the current shape:

```yaml
reporter: true
```

or:

```yaml
reporter:
  base_url: "${ONESTEP_CONTROL_PLANE_URL}"
  token: "${ONESTEP_CONTROL_PLANE_TOKEN}"
  service_name: billing-sync
```

- The future generic reporter shape is allowed but not required:

```yaml
reporter:
  type: control_plane
  base_url: "${ONESTEP_CONTROL_PLANE_URL}"
  token: "${ONESTEP_CONTROL_PLANE_TOKEN}"
```

### Task 1: Add Core Reporter Registry

**Files:**
- Create: `src/onestep/reporter_registry.py`
- Modify: `src/onestep/config.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Create failing registry tests**

Add these focused tests to `tests/test_cli.py` near the existing YAML reporter tests:

```python
def test_yaml_target_reporter_missing_plugin_has_install_hint(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "reporter-missing-plugin.yaml"
    config_path.write_text(
        json.dumps({"name": "yaml-reporter", "reporter": True, "tasks": []}),
        encoding="utf-8",
    )
    monkeypatch.setattr(config_module, "load_reporter_plugins", lambda registry=None: None)
    monkeypatch.setattr(config_module, "default_reporter_registry", lambda: config_module.ReporterRegistry())

    with registered_yaml_module(), pytest.raises(RuntimeError) as exc_info:
        OneStepApp.load(str(config_path))

    assert "pip install 'onestep[control-plane]'" in str(exc_info.value)
```

Add this registry injection test:

```python
def test_yaml_target_attaches_reporter_through_registry(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "reporter-registry.yaml"
    config_path.write_text(
        json.dumps(
            {
                "name": "yaml-reporter",
                "reporter": {"type": "control_plane", "base_url": "https://plane.example.com"},
                "tasks": [],
            }
        ),
        encoding="utf-8",
    )
    created = []
    registry = config_module.ReporterRegistry()

    def build(ctx, spec):
        created.append((ctx.app.name, dict(spec)))

        class FakeReporter:
            def attach(self, app):
                app.set_reporter_summary(
                    {
                        "type": "control_plane",
                        "base_url": spec["base_url"],
                        "service_name": app.name,
                    }
                )
                return self

        return FakeReporter()

    registry.register_reporter_type(
        config_module.ReporterSpecHandler(
            type="control_plane",
            allowed_fields=frozenset({"type", "base_url"}),
            build=build,
        )
    )
    monkeypatch.setattr(config_module, "_ensure_reporter_registry_loaded", lambda: registry)

    with registered_yaml_module():
        app = OneStepApp.load(str(config_path))

    assert created == [("yaml-reporter", {"type": "control_plane", "base_url": "https://plane.example.com"})]
    assert app.describe()["reporter"]["type"] == "control_plane"
```

- [ ] **Step 2: Run tests and confirm failure**

Run:

```bash
uv run pytest -q tests/test_cli.py::test_yaml_target_reporter_missing_plugin_has_install_hint tests/test_cli.py::test_yaml_target_attaches_reporter_through_registry
```

Expected: fails because `ReporterRegistry`, `ReporterSpecHandler`, and registry-based reporter loading do not exist.

- [ ] **Step 3: Implement `src/onestep/reporter_registry.py`**

Create `src/onestep/reporter_registry.py` with the same entry-point loading shape as `resource_registry.py`:

```python
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from importlib import metadata as importlib_metadata
from typing import TYPE_CHECKING, Any

from .resource_registry import normalize_resource_type, optional_string, validate_unknown_fields

if TYPE_CHECKING:
    from .app import OneStepApp

_ENTRY_POINT_GROUP = "onestep.reporters"


@dataclass(frozen=True)
class ReporterSpecHandler:
    type: str
    allowed_fields: frozenset[str] | None
    build: Callable[["ReporterBuildContext", Mapping[str, Any]], Any]
    validate: Callable[["ReporterValidationContext", Mapping[str, Any]], None] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "type", normalize_reporter_type(self.type))
        if self.allowed_fields is not None:
            object.__setattr__(self, "allowed_fields", frozenset(self.allowed_fields))


@dataclass(frozen=True)
class ReporterBuildContext:
    app: "OneStepApp"
    field: str = "reporter"

    def optional_string(self, mapping: Mapping[str, Any], key: str) -> str | None:
        return optional_string(mapping, key)


@dataclass(frozen=True)
class ReporterValidationContext:
    field: str = "reporter"


@dataclass
class ReporterRegistry:
    _handlers: dict[str, ReporterSpecHandler] = field(default_factory=dict)
    _loaded_entry_points: set[str] = field(default_factory=set)

    def register_reporter_type(self, handler: ReporterSpecHandler) -> None:
        reporter_type = normalize_reporter_type(handler.type)
        existing = self._handlers.get(reporter_type)
        if existing is not None:
            if existing == handler:
                return
            raise ValueError(f"reporter type {reporter_type!r} is already registered")
        self._handlers[reporter_type] = handler

    def get_reporter_handler(self, reporter_type: str) -> ReporterSpecHandler | None:
        return self._handlers.get(normalize_reporter_type(reporter_type))

    def has_entry_point_loaded(self, identity: str) -> bool:
        return identity in self._loaded_entry_points

    def mark_entry_point_loaded(self, identity: str) -> None:
        self._loaded_entry_points.add(identity)


_DEFAULT_REGISTRY = ReporterRegistry()


def default_reporter_registry() -> ReporterRegistry:
    return _DEFAULT_REGISTRY


def register_reporter_type(handler: ReporterSpecHandler) -> None:
    default_reporter_registry().register_reporter_type(handler)


def load_reporter_plugins(registry: ReporterRegistry | None = None) -> None:
    target = registry or default_reporter_registry()
    for entry_point in _reporter_entry_points():
        identity = _entry_point_identity(entry_point)
        if target.has_entry_point_loaded(identity):
            continue
        try:
            register = entry_point.load()
            if not callable(register):
                raise TypeError(f"entry point {entry_point.name!r} did not resolve to a callable")
            register(target)
        except Exception as exc:
            raise RuntimeError(f"failed to load onestep reporter plugin {entry_point.name!r}") from exc
        target.mark_entry_point_loaded(identity)


def normalize_reporter_type(value: str) -> str:
    return normalize_resource_type(value)


def missing_control_plane_plugin_error() -> RuntimeError:
    return RuntimeError(
        "control plane reporter support is provided by the onestep-control-plane plugin. "
        "Install it with `pip install 'onestep[control-plane]'`."
    )


def validate_reporter_spec(spec: Mapping[str, Any], *, registry: ReporterRegistry) -> None:
    reporter_type = str(spec.get("type") or "control_plane")
    handler = registry.get_reporter_handler(reporter_type)
    if handler is None:
        if normalize_reporter_type(reporter_type) == "control_plane":
            raise missing_control_plane_plugin_error()
        raise ValueError(f"unsupported reporter type {reporter_type!r}")
    if handler.allowed_fields is not None:
        validate_unknown_fields(spec, handler.allowed_fields, field="reporter")
    if handler.validate is not None:
        handler.validate(ReporterValidationContext(), spec)


def build_reporter(app: "OneStepApp", spec: Mapping[str, Any], *, registry: ReporterRegistry) -> Any:
    reporter_type = str(spec.get("type") or "control_plane")
    handler = registry.get_reporter_handler(reporter_type)
    if handler is None:
        if normalize_reporter_type(reporter_type) == "control_plane":
            raise missing_control_plane_plugin_error()
        raise ValueError(f"unsupported reporter type {reporter_type!r}")
    return handler.build(ReporterBuildContext(app=app), spec)


def _reporter_entry_points() -> Sequence[Any]:
    entry_points = importlib_metadata.entry_points()
    if hasattr(entry_points, "select"):
        return tuple(entry_points.select(group=_ENTRY_POINT_GROUP))
    return tuple(entry_points.get(_ENTRY_POINT_GROUP, ()))


def _entry_point_identity(entry_point: Any) -> str:
    group = getattr(entry_point, "group", _ENTRY_POINT_GROUP)
    name = getattr(entry_point, "name", "")
    value = getattr(entry_point, "value", "")
    return f"{group}:{name}:{value}"
```

- [ ] **Step 4: Wire `config.py` through the registry**

Modify `src/onestep/config.py` imports to remove direct reporter imports and add:

```python
from .reporter_registry import (
    ReporterRegistry,
    ReporterSpecHandler,
    build_reporter,
    default_reporter_registry,
    load_reporter_plugins,
    validate_reporter_spec,
)
```

Add this helper near `_ensure_resource_registry_loaded()`:

```python
def _ensure_reporter_registry_loaded() -> ReporterRegistry:
    registry = default_reporter_registry()
    load_reporter_plugins(registry)
    return registry
```

Change `_STRICT_REPORTER_FIELDS` to:

```python
_STRICT_REPORTER_FIELDS = frozenset({"type", "base_url", "token", "service_name"})
```

Change `_validate_reporter_config()` to normalize and validate through the registry:

```python
def _validate_reporter_config(raw_reporter: Any) -> None:
    if raw_reporter is None or raw_reporter is False:
        return
    if raw_reporter is True:
        raw_reporter = {"type": "control_plane"}
    if not isinstance(raw_reporter, Mapping):
        raise TypeError("'reporter' must be a boolean or mapping")
    reporter_registry = _ensure_reporter_registry_loaded()
    spec = {"type": "control_plane", **dict(raw_reporter)}
    validate_reporter_spec(spec, registry=reporter_registry)
```

Change `_attach_reporter()` to:

```python
def _attach_reporter(app: OneStepApp, raw_reporter: Any) -> None:
    if raw_reporter is None or raw_reporter is False:
        return
    if raw_reporter is True:
        spec = {"type": "control_plane"}
    elif isinstance(raw_reporter, Mapping):
        spec = {"type": "control_plane", **dict(raw_reporter)}
    else:
        raise TypeError("'reporter' must be a boolean or mapping")

    reporter_registry = _ensure_reporter_registry_loaded()
    validate_reporter_spec(spec, registry=reporter_registry)
    reporter = build_reporter(app, spec, registry=reporter_registry)
    attach = getattr(reporter, "attach", None)
    if not callable(attach):
        raise TypeError(f"reporter type {spec['type']!r} did not build an attachable reporter")
    attach(app)
```

- [ ] **Step 5: Run focused registry tests**

Run:

```bash
uv run pytest -q tests/test_cli.py::test_yaml_target_reporter_missing_plugin_has_install_hint tests/test_cli.py::test_yaml_target_attaches_reporter_through_registry
```

Expected: both tests pass.

### Task 2: Create `onestep-control-plane` Plugin

**Files:**
- Create: `plugins/onestep-control-plane/pyproject.toml`
- Create: `plugins/onestep-control-plane/README.md`
- Create: `plugins/onestep-control-plane/src/onestep_control_plane/__init__.py`
- Create: `plugins/onestep-control-plane/src/onestep_control_plane/reporter.py`
- Create: `plugins/onestep-control-plane/src/onestep_control_plane/ws.py`
- Modify: `pyproject.toml`
- Test: `plugins/onestep-control-plane/tests/test_control_plane_plugin.py`

- [ ] **Step 1: Add package metadata**

Create `plugins/onestep-control-plane/pyproject.toml`:

```toml
[project]
name = "onestep-control-plane"
version = "0.1.0"
description = "Control-plane reporter and WebSocket integration plugin for onestep."
readme = "README.md"
requires-python = ">=3.9"
license = { text = "MIT" }
dependencies = [
    "onestep>=1.5.0",
    "websockets>=12.0",
]

[project.optional-dependencies]
test = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
]

[project.entry-points."onestep.reporters"]
control_plane = "onestep_control_plane:register"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/onestep_control_plane"]

[tool.uv.sources]
onestep = { workspace = true }
```

- [ ] **Step 2: Add package README**

Create `plugins/onestep-control-plane/README.md`:

````markdown
# onestep-control-plane

Control-plane reporter and WebSocket command integration plugin for `onestep`.

```bash
pip install onestep-control-plane
```

Most applications should install it through the core extra:

```bash
pip install 'onestep[control-plane]'
```

YAML usage:

```yaml
reporter: true
```

Python usage:

```python
from onestep_control_plane import ControlPlaneReporter, ControlPlaneReporterConfig
```

Compatibility imports also work when this plugin is installed:

```python
from onestep import ControlPlaneReporter, ControlPlaneReporterConfig
from onestep.control_plane_ws import ControlPlaneWsSender
```
````

- [ ] **Step 3: Move implementation files**

Copy the existing implementations:

```bash
mkdir -p plugins/onestep-control-plane/src/onestep_control_plane
cp src/onestep/reporter.py plugins/onestep-control-plane/src/onestep_control_plane/reporter.py
cp src/onestep/control_plane_ws.py plugins/onestep-control-plane/src/onestep_control_plane/ws.py
```

Then update imports in `plugins/onestep-control-plane/src/onestep_control_plane/reporter.py`:

```python
from onestep.events import TaskEvent, TaskEventKind
from onestep.identity_store import (
    IdentityStore,
    build_default_state_dir,
    derive_replica_instance_id,
    peek_instance_id,
)
from onestep.retry import FailureKind
```

and change `_build_default_sender()` to:

```python
def _build_default_sender(config: ControlPlaneReporterConfig) -> ReporterSender:
    from .ws import ControlPlaneWsSender

    return ControlPlaneWsSender(config)
```

Update imports in `plugins/onestep-control-plane/src/onestep_control_plane/ws.py`:

```python
from .reporter import ControlPlaneReporterConfig
```

and keep TYPE_CHECKING imports local to the plugin:

```python
if TYPE_CHECKING:
    from onestep.app import OneStepApp
    from .reporter import ControlPlaneReporter
```

- [ ] **Step 4: Add plugin public exports and reporter registration**

Create `plugins/onestep-control-plane/src/onestep_control_plane/__init__.py`:

```python
from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version as _package_version
from typing import Any, Mapping

from onestep.reporter_registry import ReporterRegistry, ReporterSpecHandler

from .reporter import ControlPlaneReporter, ControlPlaneReporterConfig
from .ws import (
    AgentCommand,
    AgentHelloAck,
    AgentProtocolError,
    ControlPlaneWsSender,
    ControlPlaneWsTransport,
    DEFAULT_AGENT_CAPABILITIES,
    WS_AGENT_SUBPROTOCOL,
    WS_PROTOCOL_VERSION,
    build_control_plane_http_base_url,
    build_control_plane_ws_url,
)

try:
    __version__ = _package_version("onestep-control-plane")
except PackageNotFoundError:  # pragma: no cover - local source tree before install
    __version__ = "dev"


_CONTROL_PLANE_REPORTER_FIELDS = frozenset({"type", "base_url", "token", "service_name"})


def register(registry: ReporterRegistry) -> None:
    registry.register_reporter_type(
        ReporterSpecHandler(
            type="control_plane",
            allowed_fields=_CONTROL_PLANE_REPORTER_FIELDS,
            build=_build_control_plane_reporter,
        )
    )


def _build_control_plane_reporter(ctx: Any, spec: Mapping[str, Any]) -> ControlPlaneReporter:
    config = ControlPlaneReporterConfig.from_env(
        app_name=ctx.app.name,
        base_url=ctx.optional_string(spec, "base_url"),
        token=ctx.optional_string(spec, "token"),
        service_name=ctx.optional_string(spec, "service_name"),
    )
    return ControlPlaneReporter(config)


__all__ = [
    "AgentCommand",
    "AgentHelloAck",
    "AgentProtocolError",
    "ControlPlaneReporter",
    "ControlPlaneReporterConfig",
    "ControlPlaneWsSender",
    "ControlPlaneWsTransport",
    "DEFAULT_AGENT_CAPABILITIES",
    "WS_AGENT_SUBPROTOCOL",
    "WS_PROTOCOL_VERSION",
    "__version__",
    "build_control_plane_http_base_url",
    "build_control_plane_ws_url",
    "register",
]
```

- [ ] **Step 5: Register workspace metadata**

Modify root `pyproject.toml`:

```toml
version = "1.5.0"
```

Change extras:

```toml
control-plane = [
    "onestep-control-plane>=0.1.0",
]
```

Add `onestep-control-plane>=0.1.0` to `all` and `dev`. Add this workspace member:

```toml
"plugins/onestep-control-plane",
```

Add source mapping:

```toml
onestep-control-plane = { workspace = true }
```

- [ ] **Step 6: Add plugin registration test**

Create `plugins/onestep-control-plane/tests/test_control_plane_plugin.py`:

```python
from __future__ import annotations

from onestep import OneStepApp
from onestep.reporter_registry import ReporterRegistry, build_reporter, validate_reporter_spec
from onestep_control_plane import ControlPlaneReporter, register


def test_register_adds_control_plane_reporter_type(monkeypatch) -> None:
    monkeypatch.setenv("ONESTEP_CONTROL_URL", "https://control-plane.example.com")
    monkeypatch.setenv("ONESTEP_CONTROL_TOKEN", "secret-token")
    registry = ReporterRegistry()
    register(registry)
    spec = {"type": "control_plane"}

    validate_reporter_spec(spec, registry=registry)
    reporter = build_reporter(OneStepApp("plugin-app"), spec, registry=registry)

    assert isinstance(reporter, ControlPlaneReporter)
    assert reporter.config.base_url == "https://control-plane.example.com"
    assert reporter.config.token == "secret-token"
    assert reporter.config.service_name == "plugin-app"
```

- [ ] **Step 7: Run focused plugin test**

Run:

```bash
uv run pytest -q plugins/onestep-control-plane/tests/test_control_plane_plugin.py
```

Expected: pass.

### Task 3: Replace Core Control-Plane Modules With Compatibility Shims

**Files:**
- Modify: `src/onestep/reporter.py`
- Modify: `src/onestep/control_plane_ws.py`
- Modify: `src/onestep/__init__.py`
- Test: `tests/test_packaging.py`

- [ ] **Step 1: Add core-only import tests**

Update `tests/test_packaging.py` so the core optional dependency test expects:

```python
assert control_plane == ["onestep-control-plane>=0.1.0"]
assert "onestep-control-plane>=0.1.0" in all_extra
assert "onestep-control-plane>=0.1.0" in dev_extra
assert "websockets>=12.0" not in dependencies
assert "websockets>=12.0" not in control_plane
assert "websockets>=12.0" not in test_extra
```

Add a subprocess test:

```python
def test_control_plane_compat_import_has_install_hint_without_plugin() -> None:
    script = """
import builtins

original_import = builtins.__import__

def missing_control_plane_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name.split(".", 1)[0] == "onestep_control_plane":
        raise ImportError("No module named 'onestep_control_plane'")
    return original_import(name, globals, locals, fromlist, level)

builtins.__import__ = missing_control_plane_import

try:
    from onestep import ControlPlaneReporter
except RuntimeError as exc:
    assert "pip install 'onestep[control-plane]'" in str(exc)
else:
    raise AssertionError("expected missing control-plane plugin error")
"""
    repo_root = PYPROJECT_PATH.parent
    env = {**os.environ, "PYTHONPATH": str(repo_root / "src")}
    subprocess.run([sys.executable, "-c", script], check=True, cwd=repo_root, env=env)
```

- [ ] **Step 2: Replace `src/onestep/reporter.py`**

Replace with:

```python
from __future__ import annotations

from typing import Any

from .reporter_registry import missing_control_plane_plugin_error

_EXPORTS = {"ControlPlaneReporter", "ControlPlaneReporterConfig"}


def _load_control_plane_attr(name: str) -> Any:
    try:
        from onestep_control_plane import ControlPlaneReporter, ControlPlaneReporterConfig
    except ImportError as exc:
        raise missing_control_plane_plugin_error() from exc
    exports = {
        "ControlPlaneReporter": ControlPlaneReporter,
        "ControlPlaneReporterConfig": ControlPlaneReporterConfig,
    }
    return exports[name]


def __getattr__(name: str) -> Any:
    if name in _EXPORTS:
        return _load_control_plane_attr(name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = sorted(_EXPORTS)
```

- [ ] **Step 3: Replace `src/onestep/control_plane_ws.py`**

Replace with:

```python
from __future__ import annotations

from typing import Any

from .reporter_registry import missing_control_plane_plugin_error

_EXPORTS = {
    "AgentCommand",
    "AgentHelloAck",
    "AgentProtocolError",
    "ControlPlaneWsSender",
    "ControlPlaneWsTransport",
    "DEFAULT_AGENT_CAPABILITIES",
    "WS_AGENT_SUBPROTOCOL",
    "WS_PROTOCOL_VERSION",
    "build_control_plane_http_base_url",
    "build_control_plane_ws_url",
}


def _load_control_plane_attr(name: str) -> Any:
    try:
        import onestep_control_plane
    except ImportError as exc:
        raise missing_control_plane_plugin_error() from exc
    return getattr(onestep_control_plane, name)


def __getattr__(name: str) -> Any:
    if name in _EXPORTS:
        return _load_control_plane_attr(name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = sorted(_EXPORTS)
```

- [ ] **Step 4: Make top-level control-plane exports lazy**

Modify `src/onestep/__init__.py` to remove eager imports from `.reporter` and `.control_plane_ws`, keep the names in `__all__`, and add:

```python
_CONTROL_PLANE_EXPORTS = {
    "AgentHelloAck",
    "AgentProtocolError",
    "ControlPlaneReporter",
    "ControlPlaneReporterConfig",
    "ControlPlaneWsSender",
    "ControlPlaneWsTransport",
    "build_control_plane_http_base_url",
    "build_control_plane_ws_url",
}


def __getattr__(name: str):
    if name in _CONTROL_PLANE_EXPORTS:
        try:
            import onestep_control_plane
        except ImportError as exc:
            from .reporter_registry import missing_control_plane_plugin_error

            raise missing_control_plane_plugin_error() from exc
        return getattr(onestep_control_plane, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
```

- [ ] **Step 5: Run focused packaging tests**

Run:

```bash
uv run pytest -q tests/test_packaging.py
```

Expected: pass.

### Task 4: Move Control-Plane Tests And Update Existing Tests

**Files:**
- Create/Move: `plugins/onestep-control-plane/tests/test_control_plane_reporter.py`
- Create/Move: `plugins/onestep-control-plane/tests/test_control_plane_ws.py`
- Create/Move: `plugins/onestep-control-plane/tests/test_control_plane_reconnect.py`
- Create/Move: `plugins/onestep-control-plane/tests/test_heartbeat_reporter.py`
- Create/Move: `plugins/onestep-control-plane/tests/test_runtime_identity.py`
- Create/Move: `plugins/onestep-control-plane/tests/test_replica_identity.py`
- Create/Move: `plugins/onestep-control-plane/tests/test_runtime_descriptor.py`
- Create/Move: `plugins/onestep-control-plane/tests/test_sync_reporter.py`
- Create/Move: `plugins/onestep-control-plane/tests/control_plane_testkit.py`
- Modify: `plugins/onestep-redis/tests/test_redis_plugin.py`
- Modify: `tests/test_http_sink.py`
- Modify: `tests/test_control_plane_demo_example.py`

- [ ] **Step 1: Move tests mechanically**

Run:

```bash
mkdir -p plugins/onestep-control-plane/tests
git mv tests/control_plane_testkit.py plugins/onestep-control-plane/tests/control_plane_testkit.py
git mv tests/test_control_plane_reporter.py plugins/onestep-control-plane/tests/test_control_plane_reporter.py
git mv tests/test_control_plane_ws.py plugins/onestep-control-plane/tests/test_control_plane_ws.py
git mv tests/test_control_plane_reconnect.py plugins/onestep-control-plane/tests/test_control_plane_reconnect.py
git mv tests/test_heartbeat_reporter.py plugins/onestep-control-plane/tests/test_heartbeat_reporter.py
git mv tests/test_runtime_identity.py plugins/onestep-control-plane/tests/test_runtime_identity.py
git mv tests/test_replica_identity.py plugins/onestep-control-plane/tests/test_replica_identity.py
git mv tests/test_runtime_descriptor.py plugins/onestep-control-plane/tests/test_runtime_descriptor.py
git mv tests/test_sync_reporter.py plugins/onestep-control-plane/tests/test_sync_reporter.py
```

- [ ] **Step 2: Update imports in moved tests**

Replace:

```python
from onestep import ControlPlaneReporter, ControlPlaneReporterConfig, ControlPlaneWsSender
```

with:

```python
from onestep_control_plane import ControlPlaneReporter, ControlPlaneReporterConfig, ControlPlaneWsSender
```

Replace:

```python
from onestep.control_plane_ws import _default_connect_factory
```

with:

```python
from onestep_control_plane.ws import _default_connect_factory
```

Replace monkeypatch targets like:

```python
monkeypatch.setattr("onestep.control_plane_ws.random.random", lambda: 1.0)
monkeypatch.setattr("onestep.reporter.os.getpid", lambda: pid)
```

with:

```python
monkeypatch.setattr("onestep_control_plane.ws.random.random", lambda: 1.0)
monkeypatch.setattr("onestep_control_plane.reporter.os.getpid", lambda: pid)
```

- [ ] **Step 3: Keep topology descriptor tests with the plugin**

Move reporter topology tests out of core where they instantiate `ControlPlaneReporter`. For `tests/test_http_sink.py`, either move `test_reporter_describes_http_sink_kind_and_redacts_config` into plugin tests or change its imports to `onestep_control_plane` and let the control-plane plugin test suite own it.

- [ ] **Step 4: Update connector plugin tests that inspect reporter telemetry**

For `plugins/onestep-redis/tests/test_redis_plugin.py`, import reporter classes from `onestep_control_plane`:

```python
from onestep_control_plane import ControlPlaneReporter, ControlPlaneReporterConfig
```

Keep core runtime imports from `onestep`:

```python
from onestep import OneStepApp
```

- [ ] **Step 5: Run moved tests**

Run:

```bash
uv run pytest -q plugins/onestep-control-plane/tests
```

Expected: pass.

### Task 5: Docs, Lockfile, Reliability, And Release Prep

**Files:**
- Modify: `README.md`
- Modify: `example/README.md`
- Modify: `CHANGELOG.md`
- Modify: `scripts/run-reliability-checks.sh`
- Modify: `uv.lock`

- [ ] **Step 1: Update docs text**

In `README.md`, keep the current usage examples but clarify installation:

```markdown
Install the control-plane reporter when you need plane telemetry or remote commands:

```bash
pip install 'onestep[control-plane]'
```
```

Change the package matrix row for control-plane reporter from core-only wording to plugin wording:

```markdown
| **Operate** | control-plane reporter with remote commands | `pip install 'onestep[control-plane]'` |
```

- [ ] **Step 2: Update example docs**

In `example/README.md`, state that reporter demos require:

```bash
pip install 'onestep[control-plane]'
```

- [ ] **Step 3: Update changelog**

Add under `## Unreleased`:

```markdown
- Moves control-plane reporter and WebSocket command integration into the new `onestep-control-plane` plugin package.
- Keeps YAML `reporter: true` and legacy control-plane import paths working when `onestep[control-plane]` is installed, with a clear install hint when the plugin is missing.
- Adds the `onestep.reporters` entry-point registry so future reporters can be installed without becoming core dependencies.
```

- [ ] **Step 4: Include plugin tests in reliability script**

Add to `plugin_paths` in `scripts/run-reliability-checks.sh`:

```bash
"plugins/onestep-control-plane/tests"
```

- [ ] **Step 5: Refresh lockfile**

Run:

```bash
uv lock
```

Expected: `uv.lock` includes root `onestep` version `1.5.0`, `onestep-control-plane` version `0.1.0`, and the plugin dependency on `websockets>=12.0`.

- [ ] **Step 6: Run focused validation**

Run:

```bash
uv run pytest -q tests/test_packaging.py tests/test_cli.py::test_yaml_target_attaches_reporter_through_registry plugins/onestep-control-plane/tests
```

Expected: pass.

- [ ] **Step 7: Run broad validation**

Run:

```bash
uv run pytest -q -m "not integration"
./scripts/run-reliability-checks.sh
uv build
uv build --package onestep-control-plane
```

Expected: all commands pass and `dist/` includes both core and plugin artifacts.

## Self-Review

- Spec coverage: package name is `onestep-control-plane`; Python import package is `onestep_control_plane`; core keeps task control, identity, and metrics primitives; control-plane reporter and WebSocket code move to the plugin; compatibility import and YAML paths are preserved behind the extra.
- Placeholder scan: no `TBD`, `TODO`, or unspecified "add tests" steps remain; every task names exact files and validation commands.
- Type consistency: reporter registry uses `ReporterSpecHandler`, `ReporterBuildContext`, `ReporterValidationContext`, and `ReporterRegistry` consistently; plugin entry point is `onestep.reporters/control_plane = onestep_control_plane:register`.
