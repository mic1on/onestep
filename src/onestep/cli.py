from __future__ import annotations

import argparse
import json
import os
import sys
from importlib.metadata import PackageNotFoundError, version

from .app import OneStepApp


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run or inspect a OneStepApp target")
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {_resolve_version()}",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Load and run a OneStepApp target")
    run_parser.add_argument("target", help="Python target in the form package.module:app")

    check_parser = subparsers.add_parser("check", help="Load a target and print its task summary")
    check_parser.add_argument("target", help="Python target in the form package.module:app")
    check_parser.add_argument("--json", action="store_true", dest="as_json", help="Emit the summary as JSON")

    return parser.parse_args(_normalize_argv(argv))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    _ensure_cwd_on_syspath()
    try:
        app = OneStepApp.load(args.target)
    except Exception as exc:
        print(f"onestep: failed to load {args.target}: {exc}", file=sys.stderr)
        return 2

    if args.command == "check":
        _print_summary(args.target, app, as_json=getattr(args, "as_json", False))
        return 0

    try:
        app.run()
    except Exception as exc:
        print(f"onestep: {args.target} failed while running: {exc}", file=sys.stderr)
        return 1
    return 0


def _ensure_cwd_on_syspath() -> None:
    cwd = os.getcwd()
    if not cwd:
        return
    if cwd in sys.path:
        return
    sys.path.insert(0, cwd)


def _normalize_argv(argv: list[str] | None) -> list[str] | None:
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        return argv
    if argv[0].startswith("-") or argv[0] in {"run", "check"}:
        return argv
    return ["run", *argv]


def _print_summary(target: str, app: OneStepApp, *, as_json: bool) -> None:
    summary = {
        "target": target,
        **app.describe(),
    }
    if as_json:
        print(json.dumps(summary, indent=2))
        return

    print(f"Target: {summary['target']}")
    print(f"App: {summary['name']}")
    print(f"Shutdown timeout: {_format_timeout(summary['shutdown_timeout_s'])}")
    print(f"Tasks: {len(summary['tasks'])}")
    for task in summary["tasks"]:
        source = _format_resource(task["source"])
        emit = _format_resources(task["emit"])
        dead_letter = _format_resources(task["dead_letter"])
        timeout = _format_timeout(task["timeout_s"])
        print(
            f"- {task['name']} source={source} emit={emit} dead_letter={dead_letter} "
            f"concurrency={task['concurrency']} timeout={timeout} retry={task['retry']}"
        )


def _format_timeout(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.2f}s"


def _format_resources(items: list[dict[str, str]]) -> str:
    if not items:
        return "-"
    return ",".join(_format_resource(item) for item in items)


def _format_resource(item: dict[str, str] | None) -> str:
    if item is None:
        return "-"
    return f"{item['name']}<{item['type']}>"


def _resolve_version() -> str:
    try:
        return version("onestep")
    except PackageNotFoundError:
        return "dev"


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
