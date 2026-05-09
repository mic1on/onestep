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


def test_websockets_is_control_plane_optional_dependency_only() -> None:
    dependencies = _read_array("[project]", "dependencies")
    control_plane = _read_array("[project.optional-dependencies]", "control-plane")
    all_extra = _read_array("[project.optional-dependencies]", "all")
    dev_extra = _read_array("[project.optional-dependencies]", "dev")
    test_extra = _read_array("[project.optional-dependencies]", "test")

    assert all("websockets" not in dependency for dependency in dependencies)
    assert control_plane == ["websockets>=12.0"]
    assert "websockets>=12.0" in all_extra
    assert "websockets>=12.0" in dev_extra
    assert "websockets>=12.0" not in test_extra


def test_core_import_path_does_not_require_websockets() -> None:
    script = """
import builtins

original_import = builtins.__import__

def missing_websockets_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name == "websockets" or name.startswith("websockets."):
        raise ImportError("No module named 'websockets'")
    return original_import(name, globals, locals, fromlist, level)

builtins.__import__ = missing_websockets_import

from onestep import MemoryQueue, OneStepApp

app = OneStepApp("core-only")
queue = MemoryQueue("core.queue")
assert app.name == "core-only"
assert queue.name == "core.queue"
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
