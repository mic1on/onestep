#!/usr/bin/env python3
"""Create a minimal onestep worker project.

Uses `onestep init` when the CLI is available. Falls back to the bundled
template for environments where onestep is not installed yet.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = SKILL_DIR / "assets" / "yaml-project-template"


def package_name(project_name: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_]+", "_", project_name.strip()).strip("_").lower()
    if not normalized:
        return "worker"
    if normalized[0].isdigit():
        normalized = f"worker_{normalized}"
    return normalized


def copy_template(target: Path, *, project_name: str, force: bool) -> None:
    if target.exists() and any(target.iterdir()) and not force:
        raise SystemExit(f"target exists and is not empty: {target}")
    target.mkdir(parents=True, exist_ok=True)

    pkg = package_name(project_name)
    for source in TEMPLATE_DIR.rglob("*"):
        relative = source.relative_to(TEMPLATE_DIR)
        relative_text = str(relative).replace("src/worker", f"src/{pkg}")
        destination = target / relative_text
        if source.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            continue
        if destination.exists() and not force:
            raise SystemExit(f"refusing to overwrite: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        text = source.read_text(encoding="utf-8")
        text = text.replace("__PROJECT_NAME__", project_name)
        text = text.replace("__PACKAGE_NAME__", pkg)
        destination.write_text(text, encoding="utf-8")


def run_onestep_init(target: Path, *, force: bool) -> bool:
    if shutil.which("onestep") is None:
        return False
    cmd = ["onestep", "init", str(target)]
    if force:
        cmd.append("--force")
    completed = subprocess.run(cmd, check=False)
    return completed.returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Scaffold a minimal onestep worker project.")
    parser.add_argument("target", help="Project directory to create")
    parser.add_argument("--name", help="Project/app name. Defaults to target directory name.")
    parser.add_argument("--force", action="store_true", help="Overwrite generated files if needed.")
    parser.add_argument(
        "--template-only",
        action="store_true",
        help="Skip `onestep init` and use the bundled template.",
    )
    args = parser.parse_args()

    target = Path(args.target).resolve()
    project_name = args.name or target.name

    if not args.template_only and run_onestep_init(target, force=args.force):
        return 0

    copy_template(target, project_name=project_name, force=args.force)
    print(f"Initialized onestep worker at {target}")
    print(f"Project: {project_name}")
    print(f"Package: {package_name(project_name)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
