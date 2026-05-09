from __future__ import annotations

import argparse
import json
import os
import sys
from importlib.metadata import PackageNotFoundError, version

from .app import OneStepApp
from .config import is_yaml_target, load_yaml_app
from .init_project import init_project

_PROJECT_MARKERS = ("pyproject.toml", "setup.py", "setup.cfg")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run or inspect a OneStepApp target or YAML config")
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {_resolve_version()}",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Load and run a OneStepApp target or YAML config")
    run_parser.add_argument("target", help="Python target (package.module:app) or path to *.yaml")

    check_parser = subparsers.add_parser("check", help="Load a target or YAML config and print its task summary")
    check_parser.add_argument("target", help="Python target (package.module:app) or path to *.yaml")
    check_parser.add_argument("--json", action="store_true", dest="as_json", help="Emit the summary as JSON")
    check_parser.add_argument(
        "--strict",
        action="store_true",
        help="Validate YAML targets against the strict config contract before printing the summary",
    )

    init_parser = subparsers.add_parser("init", help="Create a minimal OneStep YAML project scaffold")
    init_parser.add_argument("path", nargs="?", default=".", help="Directory to initialize")
    init_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite scaffold files when they already exist",
    )

    return parser.parse_args(_normalize_argv(argv))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "init":
        try:
            result = init_project(args.path, force=args.force)
        except Exception as exc:
            print(f"onestep: failed to initialize {args.path}: {exc}", file=sys.stderr)
            return 2
        _print_init_summary(result)
        return 0

    _ensure_local_import_paths(args.target)
    try:
        if args.command == "check" and getattr(args, "strict", False) and is_yaml_target(args.target):
            app = load_yaml_app(args.target, strict=True)
        else:
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
        if any(os.path.exists(os.path.join(current, marker)) for marker in _PROJECT_MARKERS):
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


def _normalize_argv(argv: list[str] | None) -> list[str] | None:
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        return argv
    if argv[0].startswith("-") or argv[0] in {"run", "check", "init"}:
        return argv
    return ["run", *argv]


def _print_init_summary(result) -> None:
    print(f"Initialized OneStep project at {result.root}")
    print(f"Project: {result.project_name}")
    print(f"Package: {result.package_name}")
    print("Files:")
    for path in result.files:
        print(f"- {path}")


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
    print(f"Reporter: {_format_reporter(summary.get('reporter'))}")
    print(f"Resources: {_format_resource_inventory(summary['resources'])}")
    print(f"Hooks: {_format_hook_counts(summary['hooks'])}")
    print(f"Tasks: {len(summary['tasks'])}")
    for task in summary["tasks"]:
        source = _format_resource(task["source"])
        emit = _format_resources(task["emit"])
        dead_letter = _format_resources(task["dead_letter"])
        timeout = _format_timeout(task["timeout_s"])
        description = f" description={task['description']!r}" if task.get("description") else ""
        print(
            f"- {task['name']} source={source} emit={emit} dead_letter={dead_letter} "
            f"concurrency={task['concurrency']} timeout={timeout} retry={task['retry']}{description}"
        )
        details = _format_task_details(task)
        if details:
            print(f"  {details}")


def _format_timeout(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.2f}s"


def _format_resources(items: list[dict[str, str]]) -> str:
    if not items:
        return "-"
    return ",".join(_format_resource(item) for item in items)


def _format_resource_inventory(items: list[dict[str, str]]) -> str:
    if not items:
        return "0"
    return f"{len(items)} ({', '.join(_format_resource(item) for item in items)})"


def _format_resource(item: dict[str, str] | None) -> str:
    if item is None:
        return "-"
    return f"{item['name']}<{item['type']}>"


def _format_reporter(reporter: dict[str, str] | None) -> str:
    if not reporter:
        return "-"
    parts = [str(reporter["type"])]
    service_name = reporter.get("service_name")
    if service_name:
        parts.append(f"service={service_name}")
    base_url = reporter.get("base_url")
    if base_url:
        parts.append(f"base_url={base_url}")
    return " ".join(parts)


def _format_hook_counts(hooks: dict[str, int]) -> str:
    return " ".join(f"{name}={hooks[name]}" for name in ("startup", "shutdown", "events"))


def _format_task_details(task: dict[str, object]) -> str:
    parts: list[str] = []
    handler_ref = task.get("handler_ref")
    if isinstance(handler_ref, str) and handler_ref:
        parts.append(f"handler={handler_ref}")

    raw_hooks = task.get("hooks")
    if isinstance(raw_hooks, dict):
        hook_parts = [
            f"{name}:{raw_hooks[name]}"
            for name in ("before", "after_success", "on_failure")
            if raw_hooks[name]
        ]
        if hook_parts:
            parts.append(f"hooks={','.join(hook_parts)}")

    for field in ("config", "metadata"):
        raw_value = task.get(field)
        if isinstance(raw_value, dict) and raw_value:
            parts.append(f"{field}_keys={','.join(sorted(str(key) for key in raw_value))}")

    return " ".join(parts)


def _resolve_version() -> str:
    try:
        return version("onestep")
    except PackageNotFoundError:
        return "dev"


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
