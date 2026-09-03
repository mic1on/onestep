from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import sys
from importlib.metadata import PackageNotFoundError, version

from .app import OneStepApp
from .build import BuildOptions, BuildResult, build_worker_package
from .config import is_yaml_target, load_resource_catalog, load_yaml_app
from .diagnostics.connectivity import check_connectivity
from .diagnostics.models import (
    ConnectivityReport,
    DiagnosticReport,
    DiagnosticRequest,
)
from .diagnostics.supervisor import supervise_diagnostic
from .diagnostics.targets import _ensure_local_import_paths
from .envelope import Envelope
from .init_project import init_project
from .jsonlog import JsonLogFormatter
from .render import render_mermaid

_CLI_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


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
    run_parser.add_argument(
        "--env-file",
        dest="env_file",
        default=None,
        help="Path to a .env file to load environment variables from (YAML targets only)",
    )
    run_parser.add_argument(
        "--strict-env",
        action="store_true",
        dest="strict_env",
        default=None,
        help="Check that all ${VAR} references resolve to environment variables (YAML targets only)",
    )
    run_parser.add_argument(
        "--log-level",
        type=str.upper,
        choices=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
        default=None,
        help="Set the onestep logger level (default: target configuration or INFO)",
    )
    run_parser.add_argument(
        "--metrics-addr",
        dest="metrics_addr",
        type=str,
        default=None,
        metavar="HOST:PORT",
        help=(
            "Expose Prometheus /metrics and /healthz on HOST:PORT "
            "(use :PORT to bind all interfaces; e.g. :9100)"
        ),
    )
    run_parser.add_argument(
        "--log-format",
        dest="log_format",
        choices=("text", "json"),
        default=None,
        help=(
            "Log output format: text (default) or one-JSON-object-per-line for "
            "log collectors such as Loki/ELK; overrides YAML app.logging.format"
        ),
    )
    run_parser.add_argument(
        "--task-events",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Emit task lifecycle events as logs (default: enabled)",
    )

    check_parser = subparsers.add_parser("check", help="Load a target or YAML config and print its task summary")
    check_parser.add_argument("target", help="Python target (package.module:app) or path to *.yaml")
    check_parser.add_argument("--json", action="store_true", dest="as_json", help="Emit the summary as JSON")
    check_parser.add_argument(
        "--strict",
        action="store_true",
        help="Validate YAML targets against the strict config contract before printing the summary",
    )
    check_parser.add_argument(
        "--env-file",
        dest="env_file",
        default=None,
        help="Path to a .env file to load environment variables from (YAML targets only)",
    )
    check_parser.add_argument(
        "--strict-env",
        action="store_true",
        dest="strict_env",
        default=None,
        help="Check that all ${VAR} references resolve to environment variables (YAML targets only)",
    )
    check_parser.add_argument(
        "--connect",
        action="store_true",
        help="Probe open/close connectivity for lifecycle-capable resources",
    )
    check_parser.add_argument(
        "--connect-timeout",
        type=_positive_seconds,
        default=10.0,
        help="Per-resource open and close timeout in seconds (default: 10)",
    )

    init_parser = subparsers.add_parser("init", help="Create a minimal OneStep YAML project scaffold")
    init_parser.add_argument("path", nargs="?", default=".", help="Directory to initialize")
    init_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite scaffold files when they already exist",
    )

    build_parser = subparsers.add_parser("build", help="Build a deployable YAML worker package")
    build_parser.add_argument(
        "target",
        help="Path to worker.yaml, or a project directory with worker.yaml / [tool.onestep.build].entrypoint",
    )
    build_parser.add_argument(
        "--out",
        default="dist/worker.zip",
        help="Output zip path (default: dist/worker.zip)",
    )
    build_parser.add_argument(
        "--include",
        action="append",
        default=[],
        help="Additional file or glob pattern to include; may be repeated",
    )
    build_parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="Additional file or glob pattern to exclude; may be repeated",
    )
    build_parser.add_argument(
        "--manifest",
        default=None,
        help="Optional build manifest path (pyproject.toml, *.toml, or *.json)",
    )
    build_parser.add_argument(
        "--no-check",
        action="store_true",
        help="Skip the pre-build onestep check",
    )
    build_parser.add_argument(
        "--strict",
        action="store_true",
        help="Run the pre-build check with strict YAML validation",
    )
    build_parser.add_argument(
        "--env-file",
        dest="env_file",
        default=None,
        help="Path to a .env file to load environment variables from during the pre-build check",
    )
    build_parser.add_argument(
        "--strict-env",
        action="store_true",
        dest="strict_env",
        default=None,
        help="Check that all ${VAR} references resolve during the pre-build check",
    )
    build_parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit the build report as JSON",
    )

    render_parser = subparsers.add_parser(
        "render",
        help="Render the worker topology of a target or YAML config as a diagram",
    )
    render_parser.add_argument("target", help="Python target (package.module:app) or path to *.yaml")
    render_parser.add_argument(
        "--format",
        choices=("mermaid",),
        default="mermaid",
        help="Diagram format (default: mermaid)",
    )
    render_parser.add_argument(
        "--env-file",
        dest="env_file",
        default=None,
        help="Path to a .env file to load environment variables from (YAML targets only)",
    )
    render_parser.add_argument(
        "--strict-env",
        action="store_true",
        dest="strict_env",
        default=None,
        help="Check that all ${VAR} references resolve to environment variables (YAML targets only)",
    )

    catalog_parser = subparsers.add_parser("catalog", help="Print installed source/sink resource catalog")
    catalog_parser.add_argument("--json", action="store_true", dest="as_json", help="Emit the catalog as JSON")
    catalog_parser.add_argument(
        "--role",
        choices=("connector", "source", "sink", "state_store", "cursor_store"),
        default=None,
        help="Filter resources by catalog role",
    )

    task_parser = subparsers.add_parser(
        "task",
        help="Run local task diagnostics",
    )
    task_subparsers = task_parser.add_subparsers(
        dest="task_command",
        required=True,
    )
    task_run_parser = task_subparsers.add_parser(
        "run",
        help="Run one task attempt with a JSON input",
    )
    task_run_parser.add_argument("target")
    task_run_parser.add_argument("--task", required=True, dest="task_name")
    task_run_parser.add_argument("--input", required=True, dest="input_path")
    _add_diagnostic_options(task_run_parser)

    task_replay_parser = task_subparsers.add_parser(
        "replay",
        help="Replay one captured failure envelope",
    )
    task_replay_parser.add_argument("target")
    task_replay_parser.add_argument("--task", required=True, dest="task_name")
    task_replay_parser.add_argument(
        "--envelope",
        required=True,
        dest="capture_path",
    )
    _add_diagnostic_options(task_replay_parser)

    return parser.parse_args(_normalize_argv(argv))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "task":
        return _run_task_command(args)
    if args.command == "init":
        try:
            result = init_project(args.path, force=args.force)
        except Exception as exc:
            print(f"onestep: failed to initialize {args.path}: {exc}", file=sys.stderr)
            return 2
        _print_init_summary(result)
        return 0

    if args.command == "build":
        try:
            result = build_worker_package(
                BuildOptions(
                    target=args.target,
                    output=args.out,
                    include=tuple(args.include),
                    exclude=tuple(args.exclude),
                    manifest=args.manifest,
                    check=not args.no_check,
                    strict=args.strict,
                    env_file=args.env_file,
                    strict_env=args.strict_env,
                )
            )
        except Exception as exc:
            print(f"onestep: failed to build {args.target}: {exc}", file=sys.stderr)
            return 2
        _print_build_summary(result, as_json=getattr(args, "as_json", False))
        return 0

    if args.command == "catalog":
        try:
            entries = load_resource_catalog(role=getattr(args, "role", None))
        except Exception as exc:
            print(f"onestep: failed to load resource catalog: {exc}", file=sys.stderr)
            return 2
        _print_catalog_summary(entries, as_json=getattr(args, "as_json", False))
        return 0

    _ensure_local_import_paths(args.target)
    try:
        if args.command == "check" and getattr(args, "strict", False) and is_yaml_target(args.target):
            app = load_yaml_app(
                args.target,
                strict=True,
                env_file=getattr(args, "env_file", None),
                strict_env=getattr(args, "strict_env", None),
            )
        elif is_yaml_target(args.target):
            app = load_yaml_app(
                args.target,
                env_file=getattr(args, "env_file", None),
                strict_env=getattr(args, "strict_env", None),
            )
        else:
            app = OneStepApp.load(args.target)
    except Exception as exc:
        print(f"onestep: failed to load {args.target}: {exc}", file=sys.stderr)
        return 2

    if args.command == "check":
        if args.connect:
            try:
                report = asyncio.run(
                    check_connectivity(app, timeout_s=args.connect_timeout)
                )
            except Exception as exc:
                print(
                    f"onestep: connectivity check failed for {args.target}: {exc}",
                    file=sys.stderr,
                )
                return 2
            _print_connectivity_report(report, as_json=args.as_json)
            return _connectivity_exit_code(report)
        _print_summary(args.target, app, as_json=getattr(args, "as_json", False))
        return 0

    if args.command == "render":
        print(render_mermaid(app), end="")
        return 0

    metrics_addr: tuple[str, int] | None = None
    if args.metrics_addr:
        try:
            metrics_addr = _parse_metrics_addr(args.metrics_addr)
        except ValueError as exc:
            print(f"onestep: {exc}", file=sys.stderr)
            return 2

    cli_logging_state: tuple[logging.Handler, int] | None = None
    try:
        cli_logging_state = _configure_run_logging(
            explicit_level=args.log_level,
            log_format=args.log_format,
            config_format=getattr(app, "logging_format", None),
        )
        if args.task_events:
            app.enable_structured_event_logging()
        if metrics_addr is not None:
            host, port = metrics_addr
            from .observability import install_metrics

            install_metrics(app, host=host, port=port)
        app.run()
    except Exception as exc:
        print(f"onestep: {args.target} failed while running: {exc}", file=sys.stderr)
        return 1
    finally:
        if cli_logging_state is not None:
            cli_handler, previous_root_level = cli_logging_state
            root_logger = logging.getLogger()
            root_logger.removeHandler(cli_handler)
            root_logger.setLevel(previous_root_level)
            cli_handler.close()
    return 0


def _parse_metrics_addr(value: str) -> tuple[str, int]:
    """Parse ``HOST:PORT`` / ``:PORT`` / ``PORT`` for ``--metrics-addr``."""
    text = value.strip()
    if not text:
        raise ValueError("--metrics-addr must not be empty")
    if text.startswith(":"):
        host_part, port_part = "", text[1:]
    else:
        host_part, sep, port_part = text.rpartition(":")
        if not sep:
            host_part, port_part = "", text
    try:
        port = int(port_part)
    except ValueError as exc:
        raise ValueError(f"invalid --metrics-addr port in {value!r}") from exc
    if not 0 < port < 65536:
        raise ValueError(f"invalid --metrics-addr port in {value!r}")
    host = host_part.strip("[]") or "127.0.0.1"
    return host, port


def _configure_run_logging(
    *,
    explicit_level: str | None,
    log_format: str | None = None,
    config_format: str | None = None,
) -> tuple[logging.Handler, int] | None:
    framework_logger = logging.getLogger("onestep")
    if explicit_level is not None:
        framework_logger.setLevel(getattr(logging, explicit_level))
    elif framework_logger.level == logging.NOTSET:
        framework_logger.setLevel(logging.INFO)

    resolved_format = log_format or config_format or "text"
    root_logger = logging.getLogger()
    if root_logger.handlers:
        return None
    handler = logging.StreamHandler(sys.stdout)
    if resolved_format == "json":
        handler.setFormatter(JsonLogFormatter())
    else:
        handler.setFormatter(logging.Formatter(_CLI_LOG_FORMAT))
    previous_root_level = root_logger.level
    root_logger.setLevel(framework_logger.level)
    root_logger.addHandler(handler)
    return handler, previous_root_level


def _positive_seconds(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "must be a positive number of seconds"
        ) from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive number of seconds")
    return parsed


def _add_diagnostic_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--send",
        action="store_true",
        help="Perform selected sink sends instead of suppressing them",
    )
    parser.add_argument(
        "--timeout",
        type=_positive_seconds,
        default=60.0,
        help="Overall diagnostic timeout in seconds (default: 60)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit the diagnostic report as JSON",
    )


def _run_task_command(args: argparse.Namespace) -> int:
    try:
        if args.task_command == "run":
            with open(args.input_path, encoding="utf-8") as handle:
                body = json.load(handle)
            request = DiagnosticRequest(
                operation="run",
                target=args.target,
                task=args.task_name,
                envelope=Envelope(body=body),
                send=args.send,
            )
        else:
            request = DiagnosticRequest(
                operation="replay",
                target=args.target,
                task=args.task_name,
                capture_path=args.capture_path,
                send=args.send,
            )
        report = supervise_diagnostic(request, timeout_s=args.timeout)
    except Exception as exc:
        print(f"onestep: task diagnostic failed: {exc}", file=sys.stderr)
        return 2
    _print_diagnostic_report(args.target, report, as_json=args.as_json)
    print(report.warning, file=sys.stderr)
    return _diagnostic_exit_code(report)


def _normalize_argv(argv: list[str] | None) -> list[str] | None:
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        return argv
    if argv[0].startswith("-") or argv[0] in {
        "run",
        "check",
        "init",
        "build",
        "catalog",
        "render",
        "task",
    }:
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
        transforms = _format_emit_transforms(task.get("emit_bindings"))
        if transforms:
            details = " ".join(part for part in (details, f"transforms={transforms}") if part)
        if details:
            print(f"  {details}")


def _print_diagnostic_report(
    target: str,
    report: DiagnosticReport,
    *,
    as_json: bool,
) -> None:
    if as_json:
        print(json.dumps(report.to_dict(), indent=2))
        return
    print(f"Operation: {report.operation}")
    print(f"Target: {target}")
    print(f"App: {report.app}")
    print(f"Task: {report.task}")
    print(f"Mode: {report.mode}")
    print(f"Completion: {report.completion}")
    print(f"Failure stage: {report.failure_stage or '-'}")
    print(f"Selected sinks: {','.join(report.selected_sinks) or '-'}")
    print(f"Delivery action: {report.delivery_action or '-'} (predicted)")
    print(
        "Dead letter: "
        f"attempted={report.dead_letter['attempted']} "
        f"published={report.dead_letter['published']}"
    )
    print(f"Cleanup: {report.cleanup}")
    print(f"Side-effect outcome: {report.side_effect_outcome}")
    print(
        "Last checkpoint: "
        + (
            json.dumps(report.last_checkpoint, sort_keys=True)
            if report.last_checkpoint is not None
            else "-"
        )
    )
    print(f"Duration: {report.duration_s:.3f}s")
    print(f"Warning: {report.warning}")


def _print_connectivity_report(
    report: ConnectivityReport,
    *,
    as_json: bool,
) -> None:
    if as_json:
        print(json.dumps(report.to_dict(), indent=2))
        return
    print(f"App: {report.app}")
    print(f"Connectivity: {'ok' if report.ok else 'failed'}")
    print(f"Resources: {len(report.resources)}")
    for resource in report.resources:
        print(
            f"- {','.join(resource.aliases)} roles={','.join(resource.roles)} "
            f"type={resource.type_name} status={resource.status}"
        )
        if resource.open is not None:
            print(f"  open: {_format_probe_outcome(resource.open)}")
        if resource.close is not None:
            print(f"  close: {_format_probe_outcome(resource.close)}")
    for warning in report.warnings:
        print(f"Warning: {warning}")


def _format_probe_outcome(value: dict[str, object]) -> str:
    return " ".join(f"{key}={item}" for key, item in value.items())


def _diagnostic_exit_code(report: DiagnosticReport) -> int:
    if report.completion == "succeeded":
        return 0
    if report.completion == "validation_failed":
        return 2
    return 1


def _connectivity_exit_code(report: ConnectivityReport) -> int:
    return 0 if report.ok else 1


def _print_build_summary(result: BuildResult, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result.to_dict(), indent=2))
        return

    print(f"Built: {result.output}")
    print(f"Target: {result.target}")
    print(f"Entrypoint: {result.entrypoint}")
    print(f"Project root: {result.project_root}")
    print(f"Files: {len(result.files)}")
    print(f"Dependency mode: {result.dependency_mode}")
    print(f"Checksum: sha256:{result.checksum_sha256}")
    print(f"Size: {result.size_bytes} bytes")
    check_label = "skipped" if not result.check_ran else "strict" if result.strict else "yes"
    print(f"Check: {check_label}")
    if result.manifest_path is not None:
        print(f"Manifest: {result.manifest_path}")
    if result.warnings:
        print("Warnings:")
        for warning in result.warnings:
            print(f"- {warning}")


def _print_catalog_summary(entries, *, as_json: bool) -> None:
    resources = [entry.as_dict() for entry in entries]
    if as_json:
        print(json.dumps({"resources": resources}, indent=2))
        return

    print(f"Resources: {len(resources)}")
    for entry in resources:
        print(f"- {entry['type']} roles={','.join(entry['roles'])}")


def _format_timeout(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.2f}s"


def _format_resources(items: list[dict[str, str]]) -> str:
    if not items:
        return "-"
    return ",".join(_format_resource(item) for item in items)


def _format_emit_transforms(value: object) -> str:
    if not isinstance(value, list):
        return ""
    transforms: list[str] = []
    for binding in value:
        if not isinstance(binding, dict):
            continue
        transform_ref = binding.get("transform_ref")
        sink = binding.get("sink")
        if not isinstance(transform_ref, str) or not transform_ref:
            continue
        if not isinstance(sink, dict):
            continue
        name = sink.get("name")
        if isinstance(name, str) and name:
            transforms.append(f"{name}:{transform_ref}")
    return ",".join(transforms)


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
    service_description = reporter.get("service_description")
    if service_description:
        parts.append(f"description={service_description!r}")
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
