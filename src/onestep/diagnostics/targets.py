from __future__ import annotations

import os
import sys

from onestep.app import OneStepApp
from onestep.config import is_yaml_target, load_yaml_app

_PROJECT_MARKERS = ("pyproject.toml", "setup.py", "setup.cfg")


def load_diagnostic_target(target: str) -> OneStepApp:
    _ensure_local_import_paths(target)
    if is_yaml_target(target):
        return load_yaml_app(target)
    return OneStepApp.load(target)


def _ensure_local_import_paths(target: str | None = None) -> None:
    cwd = os.getcwd()
    if not cwd:
        return
    candidates: list[str] = []
    seen: set[str] = set()

    def add_candidates(base_dir: str | None) -> None:
        if base_dir is None:
            return
        for path in _candidate_import_paths(base_dir):
            normalized_path = os.path.normcase(os.path.abspath(path))
            if normalized_path in seen:
                continue
            seen.add(normalized_path)
            candidates.append(path)

    add_candidates(_target_import_root(cwd, target))
    add_candidates(cwd)

    for path in reversed(candidates):
        if _path_on_syspath(path):
            continue
        sys.path.insert(0, path)


def _candidate_import_paths(cwd: str) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()

    def add(path: str) -> None:
        absolute_path = os.path.abspath(path)
        normalized_path = os.path.normcase(absolute_path)
        if normalized_path in seen or not os.path.isdir(absolute_path):
            return
        seen.add(normalized_path)
        candidates.append(absolute_path)

    add(cwd)
    add(os.path.join(cwd, "src"))

    project_root = _find_project_root(cwd)
    if project_root is not None:
        add(project_root)
        add(os.path.join(project_root, "src"))

    return candidates


def _target_import_root(cwd: str, target: str | None) -> str | None:
    if not target or not is_yaml_target(target):
        return None
    if os.path.isabs(target):
        return os.path.dirname(target)
    return os.path.dirname(os.path.abspath(os.path.join(cwd, target)))


def _find_project_root(start: str) -> str | None:
    current = os.path.abspath(start)
    while True:
        if any(
            os.path.exists(os.path.join(current, marker))
            for marker in _PROJECT_MARKERS
        ):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def _path_on_syspath(path: str) -> bool:
    normalized_path = os.path.normcase(os.path.abspath(path))
    for entry in sys.path:
        current = entry or os.getcwd()
        if os.path.normcase(os.path.abspath(current)) == normalized_path:
            return True
    return False


__all__ = ["load_diagnostic_target"]
