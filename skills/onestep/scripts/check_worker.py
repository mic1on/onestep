#!/usr/bin/env python3
"""Run common validation for an onestep worker project."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def run(cmd: list[str], cwd: Path) -> int:
    print("+ " + " ".join(cmd))
    return subprocess.run(cmd, cwd=str(cwd), check=False).returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate an onestep worker.")
    parser.add_argument("target", nargs="?", default=".", help="Project directory or YAML file.")
    parser.add_argument("--no-strict", action="store_true", help="Use non-strict YAML check.")
    parser.add_argument("--pytest", action="store_true", help="Run pytest after onestep check.")
    parser.add_argument("--app-target", help="Python app target, e.g. worker.tasks:app.")
    args = parser.parse_args()

    target = Path(args.target).resolve()
    cwd = target.parent if target.suffix in {".yaml", ".yml"} else target

    if args.app_target:
        check_target = args.app_target
    elif target.suffix in {".yaml", ".yml"}:
        check_target = str(target)
    else:
        check_target = "worker.yaml"

    cmd = ["onestep", "check"]
    if not args.no_strict and check_target.endswith((".yaml", ".yml")):
        cmd.append("--strict")
    cmd.append(check_target)

    exit_code = run(cmd, cwd)
    if exit_code != 0:
        return exit_code

    if args.pytest:
        return run(["pytest"], cwd)

    return 0


if __name__ == "__main__":
    sys.exit(main())
