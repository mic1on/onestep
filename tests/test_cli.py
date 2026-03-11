import json
import os
import sys
import types
from contextlib import contextmanager

from onestep import MemoryQueue, OneStepApp
from onestep.cli import main


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


def clear_modules(monkeypatch, *names: str) -> None:
    for name in names:
        monkeypatch.delitem(sys.modules, name, raising=False)


def test_cli_check_prints_task_summary(capsys) -> None:
    source = MemoryQueue("incoming")
    sink = MemoryQueue("processed")
    app = OneStepApp("cli-check")

    @app.task(source=source, emit=sink, timeout_s=5.0)
    async def consume(ctx, item):
        return item

    with registered_module("testsupport_cli_check", app=app):
        exit_code = main(["check", "testsupport_cli_check:app"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Target: testsupport_cli_check:app" in captured.out
    assert "App: cli-check" in captured.out
    assert "- consume source=incoming<MemoryQueue> emit=processed<MemoryQueue>" in captured.out
    assert "timeout=5.00s" in captured.out


def test_cli_check_json_supports_factory_targets(capsys) -> None:
    def build_app() -> OneStepApp:
        app = OneStepApp("cli-factory")
        source = MemoryQueue("factory.incoming")

        @app.task(source=source)
        async def consume(ctx, item):
            return None

        return app

    with registered_module("testsupport_cli_factory", build_app=build_app):
        exit_code = main(["check", "--json", "testsupport_cli_factory:build_app"])

    captured = capsys.readouterr()
    summary = json.loads(captured.out)
    assert exit_code == 0
    assert summary["target"] == "testsupport_cli_factory:build_app"
    assert summary["name"] == "cli-factory"
    assert summary["tasks"][0]["name"] == "consume"
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


def test_cli_invalid_target_returns_non_zero(capsys) -> None:
    exit_code = main(["check", "testsupport_missing_module:app"])
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "failed to load testsupport_missing_module:app" in captured.err


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
