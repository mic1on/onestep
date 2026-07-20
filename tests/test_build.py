from __future__ import annotations

import io
import json
import zipfile

from onestep.build import BuildOptions, build_worker_package
from onestep.cli import main


def _write_worker_project(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "\n".join(
            [
                "[project]",
                'name = "build-demo"',
                'version = "0.1.0"',
                "",
                "[tool.onestep.build]",
                'include = ["templates/**", ".env"]',
                'exclude = ["templates/private/**"]',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "uv.lock").write_text("# lock\n", encoding="utf-8")
    (tmp_path / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
    src_dir = tmp_path / "src" / "demo_worker"
    src_dir.mkdir(parents=True)
    (src_dir / "__init__.py").write_text("", encoding="utf-8")
    (src_dir / "tasks.py").write_text(
        "\n".join(
            [
                "async def handle(ctx, item):",
                "    return None",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "email.txt").write_text("hello\n", encoding="utf-8")
    (tmp_path / "templates" / "private").mkdir()
    (tmp_path / "templates" / "private" / "secret.txt").write_text("secret\n", encoding="utf-8")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "ignored.py").write_text("ignored\n", encoding="utf-8")
    (tmp_path / "worker.yaml").write_text(
        "\n".join(
            [
                "apiVersion: onestep/v1alpha1",
                "kind: App",
                "app:",
                "  name: build-demo",
                "resources:",
                "  tick:",
                "    type: interval",
                "    seconds: 3600",
                "    immediate: false",
                "tasks:",
                "  - name: main",
                "    source: tick",
                "    handler:",
                "      ref: demo_worker.tasks:handle",
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_build_worker_package_collects_code_dependencies_and_manifest_includes(tmp_path):
    _write_worker_project(tmp_path)
    output = tmp_path / "dist" / "worker.zip"

    result = build_worker_package(
        BuildOptions(
            target=str(tmp_path / "worker.yaml"),
            output=str(output),
            strict=True,
        )
    )

    assert result.output == output
    assert result.entrypoint == "worker.yaml"
    assert result.dependency_mode == "pyproject"
    assert result.check_ran is True
    assert result.strict is True
    assert result.warnings == []

    with zipfile.ZipFile(output, "r") as archive:
        names = set(archive.namelist())
        manifest = json.loads(archive.read("onestep-package.json"))

    assert {
        "worker.yaml",
        "pyproject.toml",
        "uv.lock",
        "src/demo_worker/__init__.py",
        "src/demo_worker/tasks.py",
        "templates/email.txt",
        "onestep-package.json",
    }.issubset(names)
    assert "templates/private/secret.txt" not in names
    assert ".venv/ignored.py" not in names
    assert ".env" not in names
    assert manifest["format"] == "onestep.workflow_package.v1"
    assert manifest["entrypoint"] == "worker.yaml"
    assert manifest["dependency_mode"] == "pyproject"
    assert "src/demo_worker/tasks.py" in manifest["files"]


def test_cli_build_emits_json_report(tmp_path, monkeypatch, capsys):
    _write_worker_project(tmp_path)
    monkeypatch.chdir(tmp_path)

    exit_code = main(["build", "worker.yaml", "--out", "dist/worker.zip", "--json"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 0
    assert payload["entrypoint"] == "worker.yaml"
    assert payload["dependency_mode"] == "pyproject"
    assert payload["output"].endswith("dist/worker.zip")
    assert "onestep-package.json" in payload["files"]
    assert captured.err == ""


def test_build_with_no_check_warns_about_missing_callable_module(tmp_path):
    (tmp_path / "worker.yaml").write_text(
        "\n".join(
            [
                "apiVersion: onestep/v1alpha1",
                "kind: App",
                "app:",
                "  name: missing-ref",
                "resources:",
                "  tick:",
                "    type: interval",
                "    seconds: 3600",
                "tasks:",
                "  - name: main",
                "    source: tick",
                "    handler:",
                "      ref: missing_worker.tasks:handle",
                "",
            ]
        ),
        encoding="utf-8",
    )

    result = build_worker_package(
        BuildOptions(
            target=str(tmp_path / "worker.yaml"),
            output=str(tmp_path / "dist" / "worker.zip"),
            check=False,
        )
    )

    assert result.check_ran is False
    assert "could not locate local module for callable ref 'missing_worker.tasks:handle'" in result.warnings


def test_built_package_can_be_read_from_memory(tmp_path):
    _write_worker_project(tmp_path)
    output = tmp_path / "dist" / "worker.zip"

    build_worker_package(BuildOptions(target=str(tmp_path), output=str(output)))

    content = output.read_bytes()
    with zipfile.ZipFile(io.BytesIO(content), "r") as archive:
        assert archive.read("worker.yaml").startswith(b"apiVersion: onestep/v1alpha1")
