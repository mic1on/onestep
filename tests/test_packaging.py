from __future__ import annotations

import ast
import os
from pathlib import Path
import subprocess
import sys


PYPROJECT_PATH = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _read_array(section: str, key: str) -> list[str]:
    lines = PYPROJECT_PATH.read_text(encoding="utf-8").splitlines()
    in_section = False
    collecting = False
    collected: list[str] = []
    prefix = f"{key} = "

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_section = stripped == section
            if collecting:
                break
            continue
        if not in_section:
            continue
        if not collecting and stripped.startswith(prefix):
            raw_value = stripped.removeprefix(prefix).strip()
            if raw_value.endswith("]"):
                return ast.literal_eval(raw_value)
            collecting = True
            collected.append(raw_value)
            continue
        if collecting:
            collected.append(stripped)
            if stripped == "]":
                return ast.literal_eval("\n".join(collected))

    raise AssertionError(f"did not find {key!r} in {section}")


def test_control_plane_is_plugin_optional_dependency_only() -> None:
    dependencies = _read_array("[project]", "dependencies")
    control_plane = _read_array("[project.optional-dependencies]", "control-plane")
    all_extra = _read_array("[project.optional-dependencies]", "all")
    dev_extra = _read_array("[project.optional-dependencies]", "dev")
    test_extra = _read_array("[project.optional-dependencies]", "test")

    assert all("websockets" not in dependency for dependency in dependencies)
    assert control_plane == ["onestep-control-plane>=0.1.1"]
    assert "onestep-control-plane>=0.1.1" in all_extra
    assert "onestep-control-plane>=0.1.1" in dev_extra
    assert "websockets>=12.0" not in control_plane
    assert "websockets>=12.0" not in all_extra
    assert "websockets>=12.0" not in dev_extra
    assert "websockets>=12.0" not in test_extra


def test_core_import_path_does_not_require_optional_connector_dependencies() -> None:
    script = """
import builtins
import importlib.util

original_import = builtins.__import__
original_find_spec = importlib.util.find_spec

def missing_optional_find_spec(name, package=None):
    if name == "onestep_control_plane":
        return None
    return original_find_spec(name, package)

def missing_optional_import(name, globals=None, locals=None, fromlist=(), level=0):
    optional_roots = {"aio_pika", "aiormq", "boto3", "botocore", "onestep_control_plane", "pymysql", "redis", "sqlalchemy", "websockets"}
    if name.split(".", 1)[0] in optional_roots:
        raise ImportError(f"No module named {name!r}")
    return original_import(name, globals, locals, fromlist, level)

importlib.util.find_spec = missing_optional_find_spec
builtins.__import__ = missing_optional_import

from onestep import MemoryQueue, OneStepApp
from onestep import *

app = OneStepApp("core-only")
queue = MemoryQueue("core.queue")
assert app.name == "core-only"
assert queue.name == "core.queue"
assert "ControlPlaneReporter" not in globals()
"""
    repo_root = PYPROJECT_PATH.parent
    env = {
        **os.environ,
        "PYTHONPATH": str(repo_root / "src"),
    }
    subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        cwd=repo_root,
        env=env,
    )


def test_stable_core_api_exports_are_importable_without_optional_dependencies() -> None:
    script = """
import builtins

original_import = builtins.__import__

def missing_optional_import(name, globals=None, locals=None, fromlist=(), level=0):
    optional_roots = {"aio_pika", "aiormq", "boto3", "botocore", "onestep_control_plane", "pymysql", "redis", "sqlalchemy", "websockets"}
    if name.split(".", 1)[0] in optional_roots:
        raise ImportError(f"No module named {name!r}")
    return original_import(name, globals, locals, fromlist, level)

builtins.__import__ = missing_optional_import

from onestep import (
    ConnectorErrorKind,
    ConnectorOperation,
    ConnectorOperationError,
    CronSource,
    Delivery,
    Envelope,
    HttpSink,
    InMemoryMetrics,
    IntervalSource,
    MaxAttempts,
    MemoryQueue,
    NoRetry,
    OneStepApp,
    ResourceBuildContext,
    ResourceRegistry,
    ResourceSpecHandler,
    ResourceValidationContext,
    RetryAction,
    RetryDecision,
    Sink,
    Source,
    StructuredEventLogger,
    TaskContext,
    TaskEvent,
    TaskEventKind,
    WebhookSource,
    build_default_state_dir,
    derive_replica_instance_id,
    load_resource_plugins,
    register_resource_type,
)

assert OneStepApp("api").name == "api"
assert MemoryQueue("api.queue").name == "api.queue"
assert RetryDecision.RETRY.value == "retry"
assert ConnectorOperation.FETCH.value == "fetch"
assert ConnectorErrorKind.TRANSIENT.value == "transient"
assert callable(load_resource_plugins)
assert callable(register_resource_type)
assert Delivery is not None
assert Envelope is not None
assert HttpSink is not None
assert InMemoryMetrics is not None
assert IntervalSource is not None
assert MaxAttempts is not None
assert NoRetry is not None
assert ResourceBuildContext is not None
assert ResourceRegistry is not None
assert ResourceSpecHandler is not None
assert ResourceValidationContext is not None
assert RetryAction is not None
assert Sink is not None
assert Source is not None
assert StructuredEventLogger is not None
assert TaskContext is not None
assert TaskEvent is not None
assert TaskEventKind is not None
assert WebhookSource is not None
assert build_default_state_dir is not None
assert derive_replica_instance_id is not None
"""
    repo_root = PYPROJECT_PATH.parent
    env = {
        **os.environ,
        "PYTHONPATH": str(repo_root / "src"),
    }
    subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        cwd=repo_root,
        env=env,
    )


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
    env = {
        **os.environ,
        "PYTHONPATH": str(repo_root / "src"),
    }
    subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        cwd=repo_root,
        env=env,
    )
