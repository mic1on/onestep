import asyncio
import json
import logging
import os
import sys
import time
import types
from contextlib import contextmanager
from typing import Optional

import pytest

import onestep.cli as cli_module
import onestep.config as config_module
from onestep import (
    FailureCaptureConfig,
    MemoryQueue,
    OneStepApp,
    StructuredEventLogger,
    load_app_config,
)
from onestep.capture.writer import FailureCaptureWriter
from onestep.cli import (
    _connectivity_exit_code,
    _diagnostic_exit_code,
    main,
    parse_args,
)
from onestep.diagnostics.models import (
    ConnectivityReport,
    ConnectivityResourceResult,
    DiagnosticReport,
)
from onestep.envelope import Envelope
from onestep.retry import FailureInfo, FailureKind
from onestep.task import EmitBinding, EmitRoute


@contextmanager
def registered_module(name: str, **attrs):
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    previous = sys.modules.get(name)
    sys.modules[name] = module
    try:
        yield module
    finally:
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous


@contextmanager
def registered_yaml_module():
    def safe_load(handle):
        return json.loads(handle.read())

    with registered_module("yaml", safe_load=safe_load) as module:
        yield module


@contextmanager
def isolated_logging():
    root = logging.getLogger()
    framework = logging.getLogger("onestep")
    root_handlers = list(root.handlers)
    root_level = root.level
    framework_level = framework.level
    try:
        root.handlers = []
        root.setLevel(logging.WARNING)
        framework.setLevel(logging.NOTSET)
        yield root, framework
    finally:
        root.handlers = root_handlers
        root.setLevel(root_level)
        framework.setLevel(framework_level)


def clear_modules(monkeypatch, *names: str) -> None:
    for name in names:
        monkeypatch.delitem(sys.modules, name, raising=False)


def make_diagnostic_report(*, completion: str = "succeeded") -> DiagnosticReport:
    return DiagnosticReport(
        operation="run",
        app="cli-diagnostic",
        task="sync",
        mode="dry-run",
        completion=completion,
        attempts=0,
        selected_sinks=("result",),
        delivery_action="would_ack",
        delivery_action_basis="predicted",
        dead_letter={"attempted": False, "published": None},
        events=(),
        duration_s=0.01,
        last_checkpoint={
            "phase": "ack",
            "transition": "completed",
            "elapsed_s": 0.01,
        },
    )


def install_fake_control_plane_reporter(monkeypatch, created: list[object]) -> None:
    registry = config_module.ReporterRegistry()

    class FakeReporter:
        def __init__(self, config):
            self.config = config
            self.app = None
            created.append(self)

        def attach(self, app):
            self.app = app
            summary = {
                "type": "control_plane",
                "base_url": self.config.base_url,
                "service_name": self.config.service_name or app.name,
            }
            if getattr(self.config, "service_description", None):
                summary["service_description"] = self.config.service_description
            app.set_reporter_summary(summary)
            app.on_startup(lambda: None)
            app.on_shutdown(lambda: None)
            app.on_event(lambda event: None)
            return self

    def build(ctx, spec):
        base_url = (
            spec.get("base_url")
            or os.environ.get("ONESTEP_CONTROL_PLANE_URL")
            or os.environ.get("ONESTEP_CONTROL_URL")
        )
        token = (
            spec.get("token")
            or os.environ.get("ONESTEP_CONTROL_PLANE_TOKEN")
            or os.environ.get("ONESTEP_CONTROL_TOKEN")
        )
        config = types.SimpleNamespace(
            base_url=base_url,
            token=token,
            service_name=spec.get("service_name") or ctx.app.name,
            service_description=spec.get("service_description"),
        )
        return FakeReporter(config)

    registry.register_reporter_type(
        config_module.ReporterSpecHandler(
            type="control_plane",
            allowed_fields=frozenset(
                {"type", "base_url", "token", "service_name", "service_description"}
            ),
            build=build,
        )
    )
    monkeypatch.setattr(config_module, "_ensure_reporter_registry_loaded", lambda: registry)


def test_cli_check_prints_task_summary(capsys) -> None:
    source = MemoryQueue("incoming")
    sink = MemoryQueue("processed")
    app = OneStepApp("cli-check")

    @app.task(
        source=source,
        emit=sink,
        timeout_s=5.0,
        description="Process incoming jobs and forward them to the processed queue.",
    )
    async def consume(ctx, item):
        return item

    with registered_module("testsupport_cli_check", app=app):
        exit_code = main(["check", "testsupport_cli_check:app"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Target: testsupport_cli_check:app" in captured.out
    assert "App: cli-check" in captured.out
    assert "Reporter: -" in captured.out
    assert "Resources: 0" in captured.out
    assert "Hooks: startup=0 shutdown=0 events=0" in captured.out
    assert "- consume source=incoming<MemoryQueue> emit=processed<MemoryQueue>" in captured.out
    assert "timeout=5.00s" in captured.out
    assert "description='Process incoming jobs and forward them to the processed queue.'" in captured.out


def test_cli_check_json_supports_factory_targets(capsys) -> None:
    def build_app() -> OneStepApp:
        app = OneStepApp("cli-factory")
        source = MemoryQueue("factory.incoming")

        @app.task(source=source)
        async def consume(ctx, item):
            """Consume a single item from the factory queue."""
            return None

        return app

    with registered_module("testsupport_cli_factory", build_app=build_app):
        exit_code = main(["check", "--json", "testsupport_cli_factory:build_app"])

    captured = capsys.readouterr()
    summary = json.loads(captured.out)
    assert exit_code == 0
    assert summary["target"] == "testsupport_cli_factory:build_app"
    assert summary["name"] == "cli-factory"
    assert summary["reporter"] is None
    assert summary["tasks"][0]["name"] == "consume"
    assert summary["tasks"][0]["description"] == "Consume a single item from the factory queue."
    assert summary["tasks"][0]["source"] == {"name": "factory.incoming", "type": "MemoryQueue"}


def test_cli_run_shorthand_executes_target(capsys) -> None:
    app = OneStepApp("cli-run")

    @app.on_startup
    def started() -> None:
        print("cli target started")

    with registered_module("testsupport_cli_run", app=app):
        exit_code = main(["testsupport_cli_run:app"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "cli target started" in captured.out


def test_cli_run_parses_logging_options() -> None:
    args = parse_args(
        ["run", "worker:app", "--log-level", "debug", "--no-task-events"]
    )

    assert args.log_level == "DEBUG"
    assert args.task_events is False


def test_cli_run_enables_task_events_by_default() -> None:
    args = parse_args(["run", "worker:app"])

    assert args.log_level is None
    assert args.task_events is True


def test_task_command_bypasses_legacy_run_shorthand() -> None:
    args = parse_args(
        [
            "task",
            "run",
            "pkg.jobs:app",
            "--task",
            "sync",
            "--input",
            "input.json",
        ]
    )
    assert (args.command, args.task_command, args.target) == (
        "task",
        "run",
        "pkg.jobs:app",
    )
    assert args.timeout == 60.0
    assert args.send is False


def test_task_replay_parser_defaults_and_legacy_shorthand() -> None:
    args = parse_args(
        [
            "task",
            "replay",
            "pkg.jobs:app",
            "--task",
            "sync",
            "--envelope",
            "capture.json",
        ]
    )
    assert args.timeout == 60.0
    assert args.send is False
    assert parse_args(["run", "task"]).target == "task"
    shorthand = parse_args(["pkg.jobs:app"])
    assert (shorthand.command, shorthand.target) == ("run", "pkg.jobs:app")


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf"])
def test_task_parser_rejects_non_positive_or_non_finite_timeout(value: str) -> None:
    with pytest.raises(SystemExit) as raised:
        parse_args(
            [
                "task",
                "run",
                "pkg.jobs:app",
                "--task",
                "sync",
                "--input",
                "input.json",
                "--timeout",
                value,
            ]
        )
    assert raised.value.code == 2


def test_check_connect_parser_defaults() -> None:
    args = parse_args(["check", "pkg.jobs:app", "--connect"])
    assert args.connect is True
    assert args.connect_timeout == 10.0


def test_task_run_accepts_any_single_json_value(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    input_path = tmp_path / "input.json"
    input_path.write_text("42", encoding="utf-8")
    observed = {}

    def supervise(request, *, timeout_s):
        observed["request"] = request
        observed["timeout_s"] = timeout_s
        return make_diagnostic_report()

    monkeypatch.setattr(cli_module, "supervise_diagnostic", supervise)
    exit_code = main(
        [
            "task",
            "run",
            "pkg.jobs:app",
            "--task",
            "sync",
            "--input",
            str(input_path),
            "--json",
        ]
    )
    captured = capsys.readouterr()
    document = json.loads(captured.out)
    assert exit_code == 0
    assert document["schema"] == "onestep/diagnostic-result"
    assert observed["request"].envelope.body == 42
    assert observed["timeout_s"] == 60.0
    assert "handler and task hooks" in captured.err


def test_task_replay_defers_validation_and_passes_capture_path(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    capture_path = tmp_path / "capture.json"
    capture_path.write_text("{}", encoding="utf-8")
    observed = {}

    def reject_parent_target_load(target):
        raise AssertionError("replay target must load only in the supervised child")

    def reject_parent_capture_load(*args, **kwargs):
        raise AssertionError("replay capture must load only in the supervised child")

    def supervise(request, *, timeout_s):
        observed["request"] = request
        return make_diagnostic_report()

    monkeypatch.setattr(
        cli_module,
        "load_diagnostic_target",
        reject_parent_target_load,
        raising=False,
    )
    monkeypatch.setattr(
        cli_module,
        "load_capture",
        reject_parent_capture_load,
        raising=False,
    )
    monkeypatch.setattr(cli_module, "supervise_diagnostic", supervise)
    assert (
        main(
            [
                "task",
                "replay",
                "pkg.jobs:app",
                "--task",
                "sync",
                "--envelope",
                str(capture_path),
            ]
        )
        == 0
    )
    captured = capsys.readouterr()
    assert observed["request"].capture_path == str(capture_path)
    assert "Completion: succeeded" in captured.out
    assert "Warning:" in captured.out


def test_task_replay_json_does_not_load_target_in_parent(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    capture_path = tmp_path / "capture.json"
    capture_path.write_text("{}", encoding="utf-8")

    def load_target(target):
        raise AssertionError("target must load only in the supervised child")

    monkeypatch.setattr(
        cli_module,
        "load_diagnostic_target",
        load_target,
        raising=False,
    )
    monkeypatch.setattr(
        cli_module,
        "supervise_diagnostic",
        lambda request, *, timeout_s: make_diagnostic_report(),
    )
    assert (
        main(
            [
                "task",
                "replay",
                "pkg.jobs:app",
                "--task",
                "sync",
                "--envelope",
                str(capture_path),
                "--json",
            ]
        )
        == 0
    )
    captured = capsys.readouterr()
    assert json.loads(captured.out)["completion"] == "succeeded"
    assert "target import output" not in captured.err


def test_task_replay_imports_target_once_and_bounds_parent_wall_time(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    capture_path = asyncio.run(
        FailureCaptureWriter(
            FailureCaptureConfig(directory=tmp_path / "captures")
        ).write(
            app="diagnostic-replay-import",
            task="replay",
            stage="handler",
            terminal=True,
            failure=FailureInfo(
                kind=FailureKind.ERROR,
                exception_type="ValueError",
                message="expected",
            ),
            envelope=Envelope(body={"value": 1}),
        )
    )
    marker = tmp_path / "imports.log"
    monkeypatch.setenv("ONESTEP_REPLAY_IMPORT_MARKER", str(marker))
    monkeypatch.setenv("ONESTEP_REPLAY_IMPORT_DELAY_S", "0.4")

    started = time.monotonic()
    exit_code = main(
        [
            "task",
            "replay",
            "tests.assets.diagnostic_replay_import:app",
            "--task",
            "replay",
            "--envelope",
            str(capture_path),
            "--timeout",
            "0.05",
            "--json",
        ]
    )
    elapsed = time.monotonic() - started

    document = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert document["completion"] == "timed_out"
    # Spawn startup time varies across supported Python versions and CI runners.
    # Keep enough headroom for that cost while still catching a second 0.4s import.
    assert elapsed < 1.0
    assert marker.read_text(encoding="utf-8").splitlines() == ["imported"]


def test_task_replay_validation_failure_is_reported_from_child(
    tmp_path,
    capsys,
) -> None:
    capture_path = asyncio.run(
        FailureCaptureWriter(
            FailureCaptureConfig(directory=tmp_path / "captures")
        ).write(
            app="wrong-app",
            task="success",
            stage="handler",
            terminal=True,
            failure=FailureInfo(
                kind=FailureKind.ERROR,
                exception_type="ValueError",
                message="expected",
            ),
            envelope=Envelope(body={"value": 1}),
        )
    )

    exit_code = main(
        [
            "task",
            "replay",
            "tests.assets.diagnostic_app:app",
            "--task",
            "success",
            "--envelope",
            str(capture_path),
            "--timeout",
            "2",
            "--json",
        ]
    )

    document = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert document["completion"] == "validation_failed"
    assert document["failure_stage"] == "child_start"


@pytest.mark.parametrize(
    "completion,expected",
    [
        ("succeeded", 0),
        ("failed", 1),
        ("timed_out", 1),
        ("child_failed", 1),
        ("validation_failed", 2),
    ],
)
def test_task_exit_codes(completion: str, expected: int) -> None:
    assert _diagnostic_exit_code(
        make_diagnostic_report(completion=completion)
    ) == expected


@pytest.mark.parametrize("contents", ["{", ""])
def test_task_run_invalid_json_returns_validation_error(
    contents,
    tmp_path,
    capsys,
) -> None:
    input_path = tmp_path / "input.json"
    input_path.write_text(contents, encoding="utf-8")
    assert (
        main(
            [
                "task",
                "run",
                "pkg.jobs:app",
                "--task",
                "sync",
                "--input",
                str(input_path),
            ]
        )
        == 2
    )
    assert "task diagnostic failed" in capsys.readouterr().err


def test_task_run_unreadable_input_returns_validation_error(tmp_path, capsys) -> None:
    missing = tmp_path / "missing.json"
    assert (
        main(
            [
                "task",
                "run",
                "pkg.jobs:app",
                "--task",
                "sync",
                "--input",
                str(missing),
            ]
        )
        == 2
    )
    assert "task diagnostic failed" in capsys.readouterr().err


def test_check_connect_renders_all_resources_and_exit_code(
    monkeypatch,
    capsys,
) -> None:
    app = OneStepApp("connect-cli")
    report = ConnectivityReport(
        app="connect-cli",
        resources=(
            ConnectivityResourceResult(
                aliases=("queue",),
                roles=("source",),
                type_name="example.Queue",
                probe_kind="lifecycle",
                status="failed",
                open={"status": "connected"},
                close={"status": "failed", "exception_type": "RuntimeError"},
            ),
        ),
        ok=False,
    )

    async def check(app_value, *, timeout_s):
        assert app_value is app
        assert timeout_s == 2.0
        return report

    monkeypatch.setattr(cli_module, "check_connectivity", check)
    with registered_module("testsupport_connect_cli", app=app):
        exit_code = main(
            [
                "check",
                "testsupport_connect_cli:app",
                "--connect",
                "--connect-timeout",
                "2",
            ]
        )
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "queue roles=source" in captured.out
    assert "open: status=connected" in captured.out
    assert "close: status=failed exception_type=RuntimeError" in captured.out
    assert _connectivity_exit_code(
        ConnectivityReport(app="ok", resources=(), ok=True)
    ) == 0


def test_cli_run_configures_info_stdout_logging_and_task_events(capsys) -> None:
    app = OneStepApp("cli-logging-default")
    observed = {}

    def run() -> None:
        observed["level"] = logging.getLogger("onestep").level
        observed["root_level"] = logging.getLogger().level
        observed["event_hooks"] = app.describe()["hooks"]["events"]
        logging.getLogger("xxxx.kpi_sync").info("business message")

    app.run = run
    with isolated_logging() as (root, _), registered_module(
        "testsupport_cli_logging", app=app
    ):
        assert main(["run", "testsupport_cli_logging:app"]) == 0
        assert root.handlers == []
        assert root.level == logging.WARNING

    assert observed == {
        "level": logging.INFO,
        "root_level": logging.INFO,
        "event_hooks": 1,
    }
    assert "business message" in capsys.readouterr().out


def test_cli_run_explicit_log_level_wins() -> None:
    app = OneStepApp("cli-logging-debug")
    observed = {}
    app.run = lambda: observed.update(
        framework_level=logging.getLogger("onestep").level,
        root_level=logging.getLogger().level,
    )

    with isolated_logging(), registered_module("testsupport_cli_debug", app=app):
        assert (
            main(
                [
                    "run",
                    "testsupport_cli_debug:app",
                    "--log-level",
                    "DEBUG",
                ]
            )
            == 0
        )

    assert observed == {
        "framework_level": logging.DEBUG,
        "root_level": logging.DEBUG,
    }


def test_cli_run_preserves_target_log_level() -> None:
    app = OneStepApp("cli-target-log-level")
    observed = {}
    app.run = lambda: observed.update(
        framework_level=logging.getLogger("onestep").level,
        root_level=logging.getLogger().level,
    )

    with isolated_logging() as (_, framework), registered_module(
        "testsupport_cli_target_level", app=app
    ):
        framework.setLevel(logging.ERROR)
        assert main(["run", "testsupport_cli_target_level:app"]) == 0

    assert observed == {
        "framework_level": logging.ERROR,
        "root_level": logging.ERROR,
    }


def test_cli_run_can_disable_automatic_task_events() -> None:
    app = OneStepApp("cli-no-task-events")
    app.run = lambda: None

    with isolated_logging(), registered_module(
        "testsupport_cli_no_events", app=app
    ):
        assert (
            main(
                [
                    "run",
                    "testsupport_cli_no_events:app",
                    "--no-task-events",
                ]
            )
            == 0
        )

    assert app.describe()["hooks"]["events"] == 0


@pytest.mark.parametrize("disable_automatic", [False, True])
def test_cli_run_preserves_registered_structured_event_logger(
    disable_automatic: bool,
) -> None:
    app = OneStepApp("cli-custom-task-events")
    custom = StructuredEventLogger(logger=logging.getLogger("custom.events"))
    app.on_event(custom)
    app.run = lambda: None
    argv = ["run", "testsupport_cli_custom_events:app"]
    if disable_automatic:
        argv.append("--no-task-events")

    with isolated_logging(), registered_module(
        "testsupport_cli_custom_events", app=app
    ):
        assert main(argv) == 0

    assert app.enable_structured_event_logging() is custom
    assert app.describe()["hooks"]["events"] == 1


def test_cli_run_preserves_existing_root_handler() -> None:
    app = OneStepApp("cli-existing-handler")
    app.run = lambda: None
    existing = logging.StreamHandler()
    formatter = logging.Formatter("CUSTOM %(message)s")
    existing.setFormatter(formatter)

    with isolated_logging() as (root, _), registered_module(
        "testsupport_cli_existing_handler", app=app
    ):
        root.addHandler(existing)
        assert main(["run", "testsupport_cli_existing_handler:app"]) == 0
        assert root.handlers == [existing]
        assert root.level == logging.WARNING
        assert existing.formatter is formatter


def test_cli_run_cleans_up_stdout_handler_after_failure(capsys) -> None:
    app = OneStepApp("cli-run-failure")

    def fail() -> None:
        raise RuntimeError("boom")

    app.run = fail
    with isolated_logging() as (root, _), registered_module(
        "testsupport_cli_run_failure", app=app
    ):
        assert main(["run", "testsupport_cli_run_failure:app"]) == 1
        assert root.handlers == []
        assert root.level == logging.WARNING

    assert "testsupport_cli_run_failure:app failed while running: boom" in (
        capsys.readouterr().err
    )


def test_cli_check_does_not_configure_logging_or_task_events() -> None:
    app = OneStepApp("cli-check-logging")

    with isolated_logging() as (root, framework), registered_module(
        "testsupport_cli_check_logging", app=app
    ):
        assert main(["check", "testsupport_cli_check_logging:app"]) == 0
        assert root.handlers == []
        assert framework.level == logging.NOTSET

    assert app.describe()["hooks"]["events"] == 0


def test_cli_invalid_target_returns_non_zero(capsys) -> None:
    exit_code = main(["check", "testsupport_missing_module:app"])
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "failed to load testsupport_missing_module:app" in captured.err


def test_cli_catalog_json_outputs_installed_resources(capsys) -> None:
    exit_code = main(["catalog", "--json"])

    captured = capsys.readouterr()
    body = json.loads(captured.out)
    resources = {item["type"]: item for item in body["resources"]}
    assert exit_code == 0
    assert set(resources) >= {"memory", "interval", "cron", "webhook", "http_sink"}
    assert resources["memory"]["roles"] == ["source", "sink"]
    assert resources["http_sink"]["fields"][1] == {
        "name": "url",
        "type": "string",
        "required": True,
        "secret": True,
    }


def test_cli_init_creates_minimal_project(tmp_path, monkeypatch, capsys) -> None:
    project_dir = tmp_path / "billing-sync"

    exit_code = main(["init", str(project_dir)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert f"Initialized OneStep project at {project_dir.resolve()}" in captured.out
    assert "Project: billing-sync" in captured.out
    assert "Package: billing_sync" in captured.out
    assert (project_dir / "pyproject.toml").exists()
    assert (project_dir / "worker.yaml").exists()
    assert (project_dir / "README.md").exists()
    assert (project_dir / "src" / "billing_sync" / "tasks" / "__init__.py").exists()
    assert (project_dir / "src" / "billing_sync" / "tasks" / "demo.py").exists()
    assert (project_dir / "src" / "billing_sync" / "transforms" / "__init__.py").exists()
    assert (project_dir / "src" / "billing_sync" / "transforms" / "demo.py").exists()
    assert not (project_dir / "src" / "billing_sync" / "hooks.py").exists()

    monkeypatch.chdir(project_dir)
    monkeypatch.setattr(
        sys,
        "path",
        [
            entry
            for entry in sys.path
            if os.path.abspath(entry)
            not in {os.path.abspath(str(project_dir)), os.path.abspath(str(project_dir / "src"))}
        ],
    )
    clear_modules(
        monkeypatch,
        "billing_sync",
        "billing_sync.tasks",
        "billing_sync.tasks.demo",
        "billing_sync.transforms",
        "billing_sync.transforms.demo",
    )

    exit_code = main(["check", "worker.yaml"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "App: billing-sync" in captured.out
    assert "handler=billing_sync.tasks.demo:run_demo" in captured.out


def test_cli_init_rejects_existing_files_without_force(tmp_path, capsys) -> None:
    project_dir = tmp_path / "existing-worker"
    project_dir.mkdir()
    existing_worker = project_dir / "worker.yaml"
    existing_worker.write_text("name: keep-me\n", encoding="utf-8")

    exit_code = main(["init", str(project_dir)])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert f"failed to initialize {project_dir}" in captured.err
    assert existing_worker.read_text(encoding="utf-8") == "name: keep-me\n"


def test_cli_init_force_overwrites_existing_files(tmp_path, capsys) -> None:
    project_dir = tmp_path / "existing-worker"
    project_dir.mkdir()
    existing_worker = project_dir / "worker.yaml"
    existing_worker.write_text("name: keep-me\n", encoding="utf-8")

    exit_code = main(["init", str(project_dir), "--force"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Project: existing-worker" in captured.out
    assert "apiVersion: onestep/v1alpha1" in existing_worker.read_text(encoding="utf-8")


def test_cli_check_loads_modules_from_current_working_directory(tmp_path, monkeypatch, capsys) -> None:
    example_dir = tmp_path / "example"
    example_dir.mkdir()
    (example_dir / "cli_app.py").write_text(
        "\n".join(
            [
                "from onestep import OneStepApp",
                "",
                "app = OneStepApp('cwd-cli-app')",
                "",
                "@app.task()",
                "async def consume(ctx, item):",
                "    return None",
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        sys,
        "path",
        [entry for entry in sys.path if os.path.abspath(entry) != os.path.abspath(str(tmp_path))],
    )
    clear_modules(monkeypatch, "example", "example.cli_app")

    exit_code = main(["check", "example.cli_app:app"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "App: cwd-cli-app" in captured.out


def test_cli_check_loads_modules_from_src_layout(tmp_path, monkeypatch, capsys) -> None:
    src_dir = tmp_path / "src" / "example"
    src_dir.mkdir(parents=True)
    (src_dir / "__init__.py").write_text("", encoding="utf-8")
    (src_dir / "cli_app.py").write_text(
        "\n".join(
            [
                "from onestep import OneStepApp",
                "",
                "app = OneStepApp('src-cli-app')",
                "",
                "@app.task()",
                "async def consume(ctx, item):",
                "    return None",
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        sys,
        "path",
        [
            entry
            for entry in sys.path
            if os.path.abspath(entry) not in {os.path.abspath(str(tmp_path)), os.path.abspath(str(tmp_path / 'src'))}
        ],
    )
    clear_modules(monkeypatch, "example", "example.cli_app")

    exit_code = main(["check", "example.cli_app:app"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "App: src-cli-app" in captured.out


def test_cli_check_loads_modules_from_project_root_when_running_in_subdirectory(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'test-app'\nversion = '0.0.0'\n", encoding="utf-8")
    src_dir = tmp_path / "src" / "example"
    src_dir.mkdir(parents=True)
    (src_dir / "__init__.py").write_text("", encoding="utf-8")
    (src_dir / "cli_app.py").write_text(
        "\n".join(
            [
                "from onestep import OneStepApp",
                "",
                "app = OneStepApp('project-root-cli-app')",
                "",
                "@app.task()",
                "async def consume(ctx, item):",
                "    return None",
                "",
            ]
        ),
        encoding="utf-8",
    )
    work_dir = tmp_path / "tools"
    work_dir.mkdir()
    monkeypatch.chdir(work_dir)
    monkeypatch.setattr(
        sys,
        "path",
        [
            entry
            for entry in sys.path
            if os.path.abspath(entry)
            not in {
                os.path.abspath(str(work_dir)),
                os.path.abspath(str(tmp_path)),
                os.path.abspath(str(tmp_path / "src")),
            }
        ],
    )
    clear_modules(monkeypatch, "example", "example.cli_app")

    exit_code = main(["check", "example.cli_app:app"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "App: project-root-cli-app" in captured.out


def test_cli_check_loads_yaml_target(capsys, tmp_path) -> None:
    config_path = tmp_path / "app.yaml"
    config_path.write_text(
        json.dumps(
            {
                "name": "yaml-cli-app",
                "connectors": {
                    "incoming": {"type": "memory"},
                    "processed": {"type": "memory"},
                },
                "tasks": [
                    {
                        "name": "consume",
                        "description": "Consume queued messages from YAML config.",
                        "source": "incoming",
                        "handler": "testsupport_yaml_cli:consume",
                        "emit": ["processed"],
                        "retry": {"type": "max_attempts", "max_attempts": 2, "delay_s": 0},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    async def consume(ctx, item):
        return item

    with registered_yaml_module(), registered_module("testsupport_yaml_cli", consume=consume):
        exit_code = main(["check", str(config_path)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert f"Target: {config_path}" in captured.out
    assert "App: yaml-cli-app" in captured.out
    assert "Reporter: -" in captured.out
    assert "Resources: 2 (incoming<MemoryQueue>, processed<MemoryQueue>)" in captured.out
    assert "Hooks: startup=0 shutdown=0 events=0" in captured.out
    assert "- consume source=incoming<MemoryQueue> emit=processed<MemoryQueue>" in captured.out
    assert "retry=MaxAttempts" in captured.out
    assert "description='Consume queued messages from YAML config.'" in captured.out
    assert "handler=testsupport_yaml_cli:consume" in captured.out


def test_cli_check_loads_yaml_handlers_from_yaml_project_src_layout(tmp_path, monkeypatch, capsys) -> None:
    project_dir = tmp_path / "yaml-project"
    project_dir.mkdir()
    (project_dir / "pyproject.toml").write_text(
        "[project]\nname = 'yaml-project'\nversion = '0.0.0'\n",
        encoding="utf-8",
    )
    src_dir = project_dir / "src" / "demo_worker"
    src_dir.mkdir(parents=True)
    (src_dir / "__init__.py").write_text("", encoding="utf-8")
    (src_dir / "tasks.py").write_text(
        "\n".join(
            [
                "from onestep import OneStepApp",
                "",
                "async def consume(ctx, item):",
                "    return None",
                "",
            ]
        ),
        encoding="utf-8",
    )
    config_path = project_dir / "worker.yaml"
    config_path.write_text(
        json.dumps(
            {
                "name": "yaml-project-app",
                "resources": {
                    "incoming": {"type": "memory"},
                },
                "tasks": [
                    {
                        "name": "consume",
                        "source": "incoming",
                        "handler": "demo_worker.tasks:consume",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    work_dir = tmp_path / "tools"
    work_dir.mkdir()
    monkeypatch.chdir(work_dir)
    monkeypatch.setattr(
        sys,
        "path",
        [
            entry
            for entry in sys.path
            if os.path.abspath(entry)
            not in {
                os.path.abspath(str(work_dir)),
                os.path.abspath(str(project_dir)),
                os.path.abspath(str(project_dir / "src")),
            }
        ],
    )
    clear_modules(monkeypatch, "demo_worker", "demo_worker.tasks")

    with registered_yaml_module():
        exit_code = main(["check", str(config_path)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "App: yaml-project-app" in captured.out
    assert "handler=demo_worker.tasks:consume" in captured.out


def test_cli_check_strict_loads_valid_yaml_target(capsys, tmp_path) -> None:
    config_path = tmp_path / "strict-app.yaml"
    config_path.write_text(
        json.dumps(
            {
                "apiVersion": "onestep/v1alpha1",
                "kind": "App",
                "app": {
                    "name": "yaml-strict-app",
                },
                "resources": {
                    "incoming": {"type": "memory", "maxsize": 100},
                },
                "tasks": [
                    {
                        "name": "consume",
                        "source": "incoming",
                        "handler": "testsupport_yaml_cli_strict:consume",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    async def consume(ctx, item):
        return None

    with registered_yaml_module(), registered_module("testsupport_yaml_cli_strict", consume=consume):
        exit_code = main(["check", "--strict", str(config_path)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "App: yaml-strict-app" in captured.out


def test_load_app_config_strict_requires_memory_maxsize() -> None:
    with pytest.raises(ValueError, match="resources.incoming.maxsize is required"):
        load_app_config(
            {
                "apiVersion": "onestep/v1alpha1",
                "kind": "App",
                "app": {
                    "name": "yaml-strict-memory",
                },
                "resources": {
                    "incoming": {"type": "memory"},
                },
                "tasks": [
                    {
                        "name": "consume",
                        "source": "incoming",
                        "handler": "testsupport_yaml_cli_strict:consume",
                    }
                ],
            },
            strict=True,
        )


def test_cli_check_strict_rejects_unknown_task_fields(capsys, tmp_path) -> None:
    config_path = tmp_path / "strict-invalid-task.yaml"
    config_path.write_text(
        json.dumps(
            {
                "apiVersion": "onestep/v1alpha1",
                "kind": "App",
                "name": "yaml-strict-invalid",
                "resources": {
                    "incoming": {"type": "memory", "maxsize": 100},
                },
                "tasks": [
                    {
                        "name": "consume",
                        "source": "incoming",
                        "handler": "testsupport_yaml_cli_invalid:consume",
                        "unexpected": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    async def consume(ctx, item):
        return None

    with registered_yaml_module(), registered_module("testsupport_yaml_cli_invalid", consume=consume):
        exit_code = main(["check", "--strict", str(config_path)])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert f"failed to load {config_path}" in captured.err
    assert "unsupported fields for tasks[0]" in captured.err


def test_cli_check_strict_rejects_mixed_app_and_legacy_root_fields(capsys, tmp_path) -> None:
    config_path = tmp_path / "strict-mixed.yaml"
    config_path.write_text(
        json.dumps(
            {
                "apiVersion": "onestep/v1alpha1",
                "kind": "App",
                "name": "legacy-name",
                "app": {
                    "name": "yaml-strict-mixed",
                },
                "tasks": [],
            }
        ),
        encoding="utf-8",
    )

    with registered_yaml_module():
        exit_code = main(["check", "--strict", str(config_path)])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "strict mode does not allow mixing 'app' with legacy top-level app fields" in captured.err


def test_cli_check_strict_rejects_unknown_reporter_fields(capsys, tmp_path) -> None:
    config_path = tmp_path / "strict-reporter.yaml"
    config_path.write_text(
        json.dumps(
            {
                "apiVersion": "onestep/v1alpha1",
                "kind": "App",
                "name": "yaml-strict-reporter",
                "reporter": {
                    "base_url": "https://control-plane.example.com",
                    "url": "https://wrong.example.com",
                },
                "tasks": [],
            }
        ),
        encoding="utf-8",
    )

    with registered_yaml_module():
        exit_code = main(["check", "--strict", str(config_path)])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "unsupported fields for reporter: url" in captured.err


def test_load_app_config_strict_applies_yaml_logging_level() -> None:
    logger = logging.getLogger("onestep")
    previous_level = logger.level
    try:
        app = load_app_config(
            {
                "apiVersion": "onestep/v1alpha1",
                "kind": "App",
                "app": {
                    "name": "yaml-logging",
                    "logging": {"level": "debug"},
                },
                "tasks": [],
            },
            strict=True,
        )
        assert app.name == "yaml-logging"
        assert logger.level == logging.DEBUG
    finally:
        logger.setLevel(previous_level)


@pytest.mark.parametrize(
    ("cli_level", "expected"),
    [(None, logging.ERROR), ("DEBUG", logging.DEBUG)],
)
def test_cli_log_level_precedence_over_yaml(
    monkeypatch,
    tmp_path,
    cli_level: Optional[str],
    expected: int,
) -> None:
    config_path = tmp_path / "logging.yaml"
    config_path.write_text(
        json.dumps(
            {
                "app": {
                    "name": "yaml-logging",
                    "logging": {"level": "ERROR"},
                },
                "tasks": [],
            }
        ),
        encoding="utf-8",
    )
    observed = {}
    monkeypatch.setattr(
        OneStepApp,
        "run",
        lambda self: observed.update(
            level=logging.getLogger("onestep").level,
            root_level=logging.getLogger().level,
            event_hooks=self.describe()["hooks"]["events"],
        ),
    )
    argv = ["run", str(config_path)]
    if cli_level is not None:
        argv.extend(["--log-level", cli_level])

    with isolated_logging(), registered_yaml_module():
        assert main(argv) == 0

    assert observed == {
        "level": expected,
        "root_level": expected,
        "event_hooks": 1,
    }


def test_load_app_config_strict_rejects_invalid_yaml_logging_level_type() -> None:
    with pytest.raises(ValueError, match="'level' must be a non-empty string"):
        load_app_config(
            {
                "apiVersion": "onestep/v1alpha1",
                "kind": "App",
                "app": {
                    "name": "yaml-logging-invalid-type",
                    "logging": {"level": 123},
                },
                "tasks": [],
            },
            strict=True,
        )


def test_load_app_config_strict_rejects_invalid_yaml_logging_level_value() -> None:
    with pytest.raises(ValueError, match="unsupported logging level 'verbose'"):
        load_app_config(
            {
                "apiVersion": "onestep/v1alpha1",
                "kind": "App",
                "app": {
                    "name": "yaml-logging-invalid-value",
                    "logging": {"level": "verbose"},
                },
                "tasks": [],
            },
            strict=True,
        )


def test_load_app_config_strict_rejects_unknown_yaml_logging_fields() -> None:
    with pytest.raises(ValueError, match="unsupported fields for app.logging: unexpected"):
        load_app_config(
            {
                "apiVersion": "onestep/v1alpha1",
                "kind": "App",
                "app": {
                    "name": "yaml-logging-invalid-field",
                    "logging": {"unexpected": True},
                },
                "tasks": [],
            },
            strict=True,
        )


def test_load_app_config_strict_rejects_unknown_nested_retry_fields() -> None:
    with pytest.raises(ValueError, match=r"unsupported fields for tasks\[0\]\.retry\.error: unexpected"):
        config_module.validate_app_config(
            {
                "apiVersion": "onestep/v1alpha1",
                "kind": "App",
                "app": {"name": "nested-retry-invalid"},
                "tasks": [
                    {
                        "handler": "testsupport_nested_retry:consume",
                        "retry": {
                            "type": "by_failure_kind",
                            "error": {
                                "type": "max_attempts",
                                "max_attempts": 2,
                                "unexpected": True,
                            },
                        },
                    }
                ],
            }
        )


def test_yaml_target_reuses_connector_instances_and_binds_handler_params(tmp_path) -> None:
    config_path = tmp_path / "pipeline.yaml"
    config_path.write_text(
        json.dumps(
            {
                "name": "yaml-pipeline",
                "connectors": {
                    "incoming": {"type": "memory"},
                    "processed": {"type": "memory"},
                },
                "tasks": [
                    {
                        "name": "double",
                        "source": "incoming",
                        "handler": {
                            "ref": "testsupport_yaml_runtime:double",
                            "params": {"factor": 2},
                        },
                        "emit": "processed",
                    },
                    {
                        "name": "collect",
                        "source": "processed",
                        "handler": "testsupport_yaml_runtime:collect",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    seen: list[dict[str, int]] = []

    async def double(ctx, item, *, factor: int):
        return {"value": item["value"] * factor}

    async def collect(ctx, item):
        seen.append(item)
        ctx.app.request_shutdown()

    async def scenario() -> None:
        with registered_yaml_module(), registered_module(
            "testsupport_yaml_runtime",
            double=double,
            collect=collect,
        ):
            app = OneStepApp.load(str(config_path))
            assert app.tasks[0].sinks[0] is app.tasks[1].source
            await app.tasks[0].source.publish({"value": 21})
            await app.serve()

    asyncio.run(scenario())
    assert seen == [{"value": 42}]


def test_yaml_target_loads_conditional_emit_routes(tmp_path) -> None:
    config_path = tmp_path / "conditional-emit.yaml"
    config_path.write_text(
        json.dumps(
            {
                "name": "yaml-conditional-emit",
                "resources": {
                    "incoming": {"type": "memory"},
                    "audit": {"type": "memory"},
                    "active": {"type": "memory"},
                    "inactive": {"type": "memory"},
                },
                "tasks": [
                    {
                        "name": "route",
                        "source": "incoming",
                        "handler": "testsupport_yaml_emit_routes:consume",
                        "emit": [
                            "audit",
                            {
                                "when": {
                                    "ref": "testsupport_yaml_emit_routes:is_active",
                                    "params": {"threshold": 10},
                                },
                                "then": "active",
                                "otherwise": "inactive",
                            },
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    async def consume(ctx, item):
        return item

    def is_active(ctx, payload, result, *, threshold: int):
        return result["value"] >= threshold

    with registered_yaml_module(), registered_module(
        "testsupport_yaml_emit_routes",
        consume=consume,
        is_active=is_active,
    ):
        app = OneStepApp.load(str(config_path))

    task = app.tasks[0]
    assert [sink.name for sink in task.sinks] == ["audit", "active", "inactive"]
    assert len(task.emit_routes) == 2
    assert task.emit_routes[0].predicate is None
    assert [sink.name for sink in task.emit_routes[0].then_sinks] == ["audit"]
    assert task.emit_routes[1].predicate_ref == "testsupport_yaml_emit_routes:is_active"
    assert [sink.name for sink in task.emit_routes[1].then_sinks] == ["active"]
    assert [sink.name for sink in task.emit_routes[1].otherwise_sinks] == ["inactive"]

    async def evaluate_predicate(result):
        selected = task.emit_routes[1].predicate(None, {}, result)
        if asyncio.iscoroutine(selected):
            return await selected
        return selected

    assert asyncio.run(evaluate_predicate({"value": 11})) is True
    assert asyncio.run(evaluate_predicate({"value": 9})) is False


def test_yaml_target_loads_per_sink_emit_binding(tmp_path) -> None:
    config_path = tmp_path / "emit-binding.yaml"
    config_path.write_text(
        json.dumps(
            {
                "apiVersion": "onestep/v1alpha1",
                "kind": "App",
                "app": {"name": "yaml-emit-binding"},
                "resources": {
                    "incoming": {"type": "memory", "maxsize": 100},
                    "audit": {"type": "memory", "maxsize": 100},
                    "projected": {"type": "memory", "maxsize": 100},
                },
                "tasks": [
                    {
                        "name": "project",
                        "source": "incoming",
                        "handler": "testsupport_yaml_emit_binding:consume",
                        "emit": [
                            "audit",
                            {
                                "sink": "projected",
                                "transform": {
                                    "ref": "testsupport_yaml_emit_binding:to_projected",
                                    "params": {"factor": 2},
                                },
                            },
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    async def consume(ctx, item):
        return item

    def to_projected(ctx, payload, result, *, factor: int):
        return {"value": result["value"] * factor}

    with registered_yaml_module(), registered_module(
        "testsupport_yaml_emit_binding",
        consume=consume,
        to_projected=to_projected,
    ):
        app = load_app_config(json.loads(config_path.read_text()), strict=True)

    task = app.tasks[0]
    assert [type(target) for target in task.emit_targets] == [EmitRoute, EmitBinding]
    assert [sink.name for sink in task.sinks] == ["audit", "projected"]
    binding = task.emit_targets[1]
    assert isinstance(binding, EmitBinding)
    assert binding.transform_ref == "testsupport_yaml_emit_binding:to_projected"
    assert asyncio.run(binding.transform(None, {}, {"value": 3})) == {"value": 6}


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        (
            {"transform": "testsupport_yaml_emit_binding:to_projected"},
            r"'sink' must be a non-empty string",
        ),
        (
            {"sink": "processed", "unknown": True},
            r"unsupported fields for tasks\[0\]\.emit\[0\]: unknown",
        ),
        (
            {"sink": "processed", "transform": {"params": {}}},
            r"'ref' must be a non-empty string",
        ),
        (
            {
                "sink": "processed",
                "when": "testsupport_yaml_emit_binding:predicate",
                "then": "processed",
            },
            r"unsupported fields for tasks\[0\]\.emit\[0\]: then, when",
        ),
    ],
)
def test_load_app_config_strict_rejects_invalid_emit_binding(entry, message) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        load_app_config(
            {
                "apiVersion": "onestep/v1alpha1",
                "kind": "App",
                "app": {"name": "yaml-invalid-emit-binding"},
                "resources": {
                    "incoming": {"type": "memory", "maxsize": 100},
                    "processed": {"type": "memory", "maxsize": 100},
                },
                "tasks": [
                    {
                        "name": "project",
                        "source": "incoming",
                        "emit": [entry],
                    }
                ],
            },
            strict=True,
        )


def test_yaml_conditional_emit_supports_list_sinks_and_passthrough_handler() -> None:
    def should_route(ctx, payload, result):
        return True

    with registered_module("testsupport_yaml_emit_passthrough", should_route=should_route):
        app = load_app_config(
            {
                "name": "yaml-conditional-passthrough",
                "resources": {
                    "incoming": {"type": "memory"},
                    "primary": {"type": "memory"},
                    "audit": {"type": "memory"},
                    "fallback": {"type": "memory"},
                },
                "tasks": [
                    {
                        "name": "route",
                        "source": "incoming",
                        "emit": [
                            {
                                "when": "testsupport_yaml_emit_passthrough:should_route",
                                "then": ["primary", "audit"],
                                "otherwise": ["fallback"],
                            }
                        ],
                    }
                ],
            }
        )

    task = app.tasks[0]
    assert task.handler_ref is None
    assert [sink.name for sink in task.sinks] == ["primary", "audit", "fallback"]
    assert [sink.name for sink in task.emit_routes[0].then_sinks] == ["primary", "audit"]
    assert [sink.name for sink in task.emit_routes[0].otherwise_sinks] == ["fallback"]


def test_cli_check_json_flattens_conditional_emit_routes(capsys, tmp_path) -> None:
    config_path = tmp_path / "conditional-cli.yaml"
    config_path.write_text(
        json.dumps(
            {
                "name": "yaml-conditional-cli",
                "resources": {
                    "incoming": {"type": "memory"},
                    "active": {"type": "memory"},
                    "inactive": {"type": "memory"},
                },
                "tasks": [
                    {
                        "name": "route",
                        "source": "incoming",
                        "emit": [
                            {
                                "when": "testsupport_yaml_emit_cli:is_active",
                                "then": "active",
                                "otherwise": "inactive",
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    def is_active(ctx, payload, result):
        return True

    with registered_yaml_module(), registered_module("testsupport_yaml_emit_cli", is_active=is_active):
        exit_code = main(["check", "--json", str(config_path)])

    captured = capsys.readouterr()
    summary = json.loads(captured.out)
    assert exit_code == 0
    assert [entry["name"] for entry in summary["tasks"][0]["emit"]] == ["active", "inactive"]
    assert "emit_routes" not in summary["tasks"][0]


def test_load_app_config_strict_rejects_emit_route_missing_then() -> None:
    with pytest.raises(ValueError, match=r"tasks\[0\]\.emit\[0\]\.then is required"):
        load_app_config(
            {
                "apiVersion": "onestep/v1alpha1",
                "kind": "App",
                "app": {"name": "yaml-strict-emit-missing-then"},
                "resources": {
                    "incoming": {"type": "memory", "maxsize": 100},
                    "processed": {"type": "memory", "maxsize": 100},
                },
                "tasks": [
                    {
                        "name": "route",
                        "source": "incoming",
                        "emit": [
                            {
                                "when": "testsupport_yaml_emit_strict:predicate",
                            }
                        ],
                    }
                ],
            },
            strict=True,
        )


def test_load_app_config_strict_rejects_emit_route_unknown_fields() -> None:
    with pytest.raises(ValueError, match=r"unsupported fields for tasks\[0\]\.emit\[0\]: else"):
        load_app_config(
            {
                "apiVersion": "onestep/v1alpha1",
                "kind": "App",
                "app": {"name": "yaml-strict-emit-unknown"},
                "resources": {
                    "incoming": {"type": "memory", "maxsize": 100},
                    "processed": {"type": "memory", "maxsize": 100},
                },
                "tasks": [
                    {
                        "name": "route",
                        "source": "incoming",
                        "emit": [
                            {
                                "when": "testsupport_yaml_emit_strict:predicate",
                                "then": "processed",
                                "else": "processed",
                            }
                        ],
                    }
                ],
            },
            strict=True,
        )


def test_load_app_config_strict_rejects_emit_route_expression_when() -> None:
    with pytest.raises(ValueError, match=r"unsupported fields for tasks\[0\]\.emit\[0\]\.when: expression"):
        load_app_config(
            {
                "apiVersion": "onestep/v1alpha1",
                "kind": "App",
                "app": {"name": "yaml-strict-emit-expression"},
                "resources": {
                    "incoming": {"type": "memory", "maxsize": 100},
                    "processed": {"type": "memory", "maxsize": 100},
                },
                "tasks": [
                    {
                        "name": "route",
                        "source": "incoming",
                        "emit": [
                            {
                                "when": {"expression": "payload.active"},
                                "then": "processed",
                            }
                        ],
                    }
                ],
            },
            strict=True,
        )


def test_load_app_config_strict_rejects_emit_route_empty_then_list() -> None:
    with pytest.raises(ValueError, match=r"tasks\[0\]\.emit\[0\]\.then must not be empty"):
        load_app_config(
            {
                "apiVersion": "onestep/v1alpha1",
                "kind": "App",
                "app": {"name": "yaml-strict-emit-empty-then"},
                "resources": {
                    "incoming": {"type": "memory", "maxsize": 100},
                    "processed": {"type": "memory", "maxsize": 100},
                },
                "tasks": [
                    {
                        "name": "route",
                        "source": "incoming",
                        "emit": [
                            {
                                "when": "testsupport_yaml_emit_strict:predicate",
                                "then": [],
                            }
                        ],
                    }
                ],
            },
            strict=True,
        )


def test_yaml_target_builds_webhook_auth_and_response(tmp_path) -> None:
    config_path = tmp_path / "webhook.yaml"
    config_path.write_text(
        json.dumps(
            {
                "name": "yaml-webhook",
                "connectors": {
                    "incoming": {
                        "type": "webhook",
                        "path": "/hooks/protected",
                        "methods": ["POST", "PUT"],
                        "auth": {
                            "type": "bearer",
                            "token": "secret-token",
                            "realm": "yaml",
                        },
                        "response": {
                            "status_code": 200,
                            "body": {"accepted": True},
                            "headers": {"X-Test": "1"},
                        },
                    },
                    "out": {"type": "memory"},
                },
                "tasks": [
                    {
                        "name": "ingest",
                        "source": "incoming",
                        "handler": "testsupport_yaml_webhook:ingest",
                        "emit": ["out"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    async def ingest(ctx, item):
        return item

    with registered_yaml_module(), registered_module("testsupport_yaml_webhook", ingest=ingest):
        app = OneStepApp.load(str(config_path))

    source = app.tasks[0].source
    status_code, headers, payload = source.response.render()

    assert source.path == "/hooks/protected"
    assert source.methods == ("POST", "PUT")
    assert source.auth.token == "secret-token"
    assert source.auth.challenge_headers()["WWW-Authenticate"] == 'Bearer realm="yaml"'
    assert status_code == 200
    assert headers["X-Test"] == "1"
    assert payload == b'{"accepted": true}'


def test_yaml_target_supports_resources_app_hooks_task_config_and_task_hooks(tmp_path) -> None:
    config_path = tmp_path / "hooks.yaml"
    config_path.write_text(
        json.dumps(
            {
                "app": {
                    "name": "yaml-hooks",
                },
                "resources": {
                    "incoming": {"type": "memory"},
                    "processed": {"type": "memory"},
                },
                "hooks": {
                    "startup": {
                        "ref": "testsupport_yaml_hooks:seed",
                        "params": {"value": 5},
                    },
                    "shutdown": "testsupport_yaml_hooks:teardown",
                    "events": {
                        "ref": "testsupport_yaml_hooks:observe",
                        "params": {"label": "audit"},
                    },
                },
                "tasks": [
                    {
                        "name": "consume",
                        "source": "incoming",
                        "emit": ["processed"],
                        "config": {"mode": "sync"},
                        "metadata": {"owner": "data-platform"},
                        "handler": {
                            "ref": "testsupport_yaml_hooks:consume",
                            "params": {"factor": 2},
                        },
                        "hooks": {
                            "before": {
                                "ref": "testsupport_yaml_hooks:before",
                                "params": {"label": "pre"},
                            },
                            "after_success": "testsupport_yaml_hooks:after",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    seen = []

    async def seed(app, *, value: int):
        seen.append(("startup", sorted(app.resources)))
        await app.resources["incoming"].publish({"value": value})

    def teardown(app):
        seen.append(("shutdown", app.name))

    def observe(event, *, label: str):
        if event.kind.value == "succeeded":
            seen.append(("event", label, event.task))

    async def before(ctx, payload, *, label: str):
        seen.append(("before", label, ctx.task_config["mode"], payload["value"]))

    async def consume(ctx, item, *, factor: int):
        seen.append(("handler", ctx.task_config["mode"], item["value"], sorted(ctx.resources)))
        ctx.app.request_shutdown()
        return {"value": item["value"] * factor}

    def after(ctx, payload, result):
        seen.append(("after", payload["value"], result["value"]))

    async def scenario() -> None:
        with registered_yaml_module(), registered_module(
            "testsupport_yaml_hooks",
            seed=seed,
            teardown=teardown,
            observe=observe,
            before=before,
            consume=consume,
            after=after,
        ):
            app = OneStepApp.load(str(config_path))
            assert sorted(app.resources) == ["incoming", "processed"]
            assert app.tasks[0].config == {"mode": "sync"}
            assert app.tasks[0].metadata == {"owner": "data-platform"}
            assert app.tasks[0].handler_ref == "testsupport_yaml_hooks:consume"
            await app.serve()
            deliveries = await app.resources["processed"].fetch(1)
            assert len(deliveries) == 1
            assert deliveries[0].payload == {"value": 10}

    asyncio.run(scenario())
    assert seen == [
        ("startup", ["incoming", "processed"]),
        ("before", "pre", "sync", 5),
        ("handler", "sync", 5, ["incoming", "processed"]),
        ("after", 5, 10),
        ("event", "audit", "consume"),
        ("shutdown", "yaml-hooks"),
    ]


def test_yaml_task_failure_hooks_receive_failure_and_ignore_secondary_failures(tmp_path) -> None:
    config_path = tmp_path / "failure-hooks.yaml"
    config_path.write_text(
        json.dumps(
            {
                "name": "yaml-failure-hooks",
                "resources": {
                    "incoming": {"type": "memory"},
                },
                "hooks": {
                    "startup": "testsupport_yaml_failure_hooks:seed",
                },
                "tasks": [
                    {
                        "name": "explode",
                        "source": "incoming",
                        "handler": "testsupport_yaml_failure_hooks:explode",
                        "hooks": {
                            "on_failure": [
                                "testsupport_yaml_failure_hooks:capture_failure",
                                "testsupport_yaml_failure_hooks:broken_failure",
                            ]
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    seen = []

    async def seed(app):
        await app.resources["incoming"].publish({"id": 7})

    async def explode(ctx, item):
        raise RuntimeError("boom")

    async def capture_failure(ctx, payload, failure):
        seen.append(("capture", payload["id"], failure.kind.value, failure.exception_type, failure.message))
        ctx.app.request_shutdown()

    def broken_failure(ctx, payload, failure):
        seen.append(("broken", failure.kind.value))
        raise RuntimeError("hook observer broke")

    async def scenario() -> None:
        with registered_yaml_module(), registered_module(
            "testsupport_yaml_failure_hooks",
            seed=seed,
            explode=explode,
            capture_failure=capture_failure,
            broken_failure=broken_failure,
        ):
            app = OneStepApp.load(str(config_path))
            await app.serve()

    asyncio.run(scenario())
    assert seen == [
        ("capture", 7, "error", "RuntimeError", "boom"),
        ("broken", "error"),
    ]


def test_yaml_target_attaches_reporter_from_env(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "reporter-bool.yaml"
    config_path.write_text(
        json.dumps(
            {
                "name": "yaml-reporter",
                "reporter": True,
                "tasks": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ONESTEP_CONTROL_URL", "https://control-plane.example.com")
    monkeypatch.setenv("ONESTEP_CONTROL_TOKEN", "secret-token")
    created = []
    install_fake_control_plane_reporter(monkeypatch, created)

    with registered_yaml_module():
        app = OneStepApp.load(str(config_path))

    assert len(created) == 1
    reporter = created[0]
    assert reporter.app is app
    assert reporter.config.base_url == "https://control-plane.example.com"
    assert reporter.config.token == "secret-token"
    assert reporter.config.service_name == "yaml-reporter"
    assert app.describe()["reporter"] == {
        "type": "control_plane",
        "base_url": "https://control-plane.example.com",
        "service_name": "yaml-reporter",
    }
    assert app.describe()["hooks"] == {"startup": 1, "shutdown": 1, "events": 1}


def test_yaml_target_reporter_missing_plugin_has_install_hint(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "reporter-missing-plugin.yaml"
    config_path.write_text(
        json.dumps(
            {
                "name": "yaml-reporter",
                "reporter": True,
                "tasks": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        config_module,
        "_ensure_reporter_registry_loaded",
        lambda: config_module.ReporterRegistry(),
    )

    with registered_yaml_module(), pytest.raises(RuntimeError) as exc_info:
        OneStepApp.load(str(config_path))

    assert "pip install 'onestep[control-plane]'" in str(exc_info.value)


def test_yaml_target_attaches_reporter_through_registry(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "reporter-registry.yaml"
    config_path.write_text(
        json.dumps(
            {
                "name": "yaml-reporter",
                "reporter": {
                    "type": "audit",
                    "endpoint": "https://audit.example.com",
                },
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
                        "type": "audit",
                        "endpoint": spec["endpoint"],
                        "service_name": app.name,
                    }
                )
                return self

        return FakeReporter()

    registry.register_reporter_type(
        config_module.ReporterSpecHandler(
            type="audit",
            allowed_fields=frozenset({"type", "endpoint"}),
            build=build,
        )
    )
    monkeypatch.setattr(config_module, "_ensure_reporter_registry_loaded", lambda: registry)

    with registered_yaml_module():
        app = OneStepApp.load(str(config_path))

    assert created == [
        (
            "yaml-reporter",
            {"type": "audit", "endpoint": "https://audit.example.com"},
        )
    ]
    assert app.describe()["reporter"] == {
        "type": "audit",
        "endpoint": "https://audit.example.com",
        "service_name": "yaml-reporter",
    }


def test_yaml_target_reporter_mapping_overrides_env_defaults(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "reporter-mapping.yaml"
    config_path.write_text(
        json.dumps(
            {
                "name": "yaml-reporter-overrides",
                "reporter": {
                    "base_url": "https://yaml-control-plane.example.com",
                    "service_name": "billing-sync-worker",
                },
                "tasks": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ONESTEP_CONTROL_URL", "https://env-control-plane.example.com")
    monkeypatch.setenv("ONESTEP_CONTROL_TOKEN", "env-token")
    created = []
    install_fake_control_plane_reporter(monkeypatch, created)

    with registered_yaml_module():
        OneStepApp.load(str(config_path))

    assert len(created) == 1
    assert created[0].config.base_url == "https://yaml-control-plane.example.com"
    assert created[0].config.token == "env-token"
    assert created[0].config.service_name == "billing-sync-worker"


def test_yaml_target_reporter_mapping_accepts_service_description(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "reporter-description.yaml"
    config_path.write_text(
        json.dumps(
            {
                "name": "yaml-reporter",
                "reporter": {
                    "base_url": "https://yaml-control-plane.example.com",
                    "service_name": "billing-sync-worker",
                    "service_description": "Synchronizes billing data.",
                },
                "tasks": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ONESTEP_CONTROL_TOKEN", "env-token")
    created = []
    install_fake_control_plane_reporter(monkeypatch, created)

    with registered_yaml_module():
        app = OneStepApp.load(str(config_path))

    assert created[0].config.service_description == "Synchronizes billing data."
    assert app.describe()["reporter"]["service_description"] == "Synchronizes billing data."


def test_cli_check_prints_reporter_summary_for_yaml_target(monkeypatch, tmp_path, capsys) -> None:
    config_path = tmp_path / "reporter-summary.yaml"
    config_path.write_text(
        json.dumps(
            {
                "name": "yaml-reporter-summary",
                "reporter": {
                    "base_url": "https://yaml-control-plane.example.com",
                    "service_name": "billing-sync-worker",
                    "service_description": "Synchronizes billing data.",
                },
                "tasks": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ONESTEP_CONTROL_TOKEN", "env-token")
    install_fake_control_plane_reporter(monkeypatch, [])

    with registered_yaml_module():
        exit_code = main(["check", str(config_path)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert (
        "Reporter: control_plane service=billing-sync-worker "
        "base_url=https://yaml-control-plane.example.com "
        "description='Synchronizes billing data.'"
    ) in captured.out
