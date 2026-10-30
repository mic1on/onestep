from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]


def executable(path: Path, body: str) -> Path:
    path.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + body)
    path.chmod(0o755)
    return path


@pytest.mark.parametrize("stage,exit_code", [("up", 11), ("setup", 17), ("pytest", 23), ("success", 0)])
@pytest.mark.parametrize("keep_services", [False, True])
def test_runner_preserves_failures_and_collects_diagnostics(
    tmp_path: Path, stage: str, exit_code: int, keep_services: bool
) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    runner = scripts / "run-integration-tests.sh"
    shutil.copyfile(ROOT / "scripts" / runner.name, runner)
    # The root tests/integration directory is intentionally absent.
    live_tests = tmp_path / "plugins/onestep-kafka/tests/integration"
    live_tests.mkdir(parents=True)
    (live_tests / "test_live.py").touch()
    calls_path = tmp_path / "calls"
    executable(scripts / "docker", '''
command_name="$4"
echo "docker $command_name" >> "$CALLS"
if [[ "$command_name" == "up" && "$STAGE" == "up" ]]; then exit 11; fi
if [[ "$command_name" == "ps" || "$command_name" == "logs" ]]; then
  echo "diagnostic: $command_name"
  exit 1  # A diagnostic failure must not replace the original failure.
fi
''')
    executable(scripts / "setup-integration-env.sh", '''
echo setup >> "$CALLS"
if [[ "$STAGE" == "setup" ]]; then
  echo 'echo unexpected-eval >> "$CALLS"'
  echo 'setup failed' >&2
  exit 17
fi
echo 'export ONESTEP_KAFKA_BOOTSTRAP_SERVERS=127.0.0.1:9092'
''')
    python = executable(scripts / "python", '''
echo "pytest $*" >> "$CALLS"
test "$ONESTEP_KAFKA_BOOTSTRAP_SERVERS" = "127.0.0.1:9092"
if [[ "$STAGE" == "pytest" ]]; then exit 23; fi
''')
    result = subprocess.run(
        ["bash", str(runner), "-k", "live"],
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": f"{scripts}{os.pathsep}{os.environ['PATH']}",
            "CALLS": str(calls_path),
            "STAGE": stage,
            "ONESTEP_PYTHON_BIN": str(python),
            "KEEP_INTEGRATION_SERVICES": "1" if keep_services else "0",
        },
        capture_output=True,
        text=True,
        timeout=10,
    )
    calls = calls_path.read_text().splitlines()
    expected = ["docker up"]
    if stage != "up":
        expected.append("setup")
    if stage in {"pytest", "success"}:
        expected.append("pytest -m pytest plugins/onestep-kafka/tests/integration -q -k live")
    if exit_code:
        expected.extend(["docker ps", "docker logs"])
        assert "diagnostic: logs" in result.stderr
    else:
        assert result.stderr == ""
    if not keep_services:
        expected.append("docker down")
    assert result.returncode == exit_code, result.stderr
    assert calls == expected


def test_setup_propagates_replica_initialization_failure(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    setup = scripts / "setup-integration-env.sh"
    shutil.copyfile(ROOT / "scripts" / setup.name, setup)
    init_dir = tmp_path / "docker/mongodb"
    init_dir.mkdir(parents=True)
    shutil.copyfile(ROOT / "docker/mongodb/init-replica-set.js", init_dir / "init-replica-set.js")
    executable(scripts / "curl", "exit 0\n")
    python = executable(scripts / "python", "cat >/dev/null\n")
    executable(scripts / "docker", "echo 'replica initialization failed' >&2\nexit 19\n")
    result = subprocess.run(
        ["bash", str(setup)],
        env={
            **os.environ,
            "PATH": f"{scripts}{os.pathsep}{os.environ['PATH']}",
            "ONESTEP_PYTHON_BIN": str(python),
        },
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 19, result.stderr
    assert "replica initialization failed" in result.stderr
    assert result.stdout == ""  # No exports may escape from a failed setup.


def test_mongodb_ci_uses_kernel_compatible_allocator_settings() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.integration.yml").read_text())
    assert compose["services"]["mongo"]["environment"]["GLIBC_TUNABLES"] == "glibc.pthread.rseq=1"
    workflow = yaml.safe_load((ROOT / ".github/workflows/plugin-mongodb.yml").read_text())
    for name in ("live-compatibility", "resilience-stepdown"):
        startup = next(step["run"] for step in workflow["jobs"][name]["steps"] if "docker run" in step.get("run", ""))
        docker_run = next(line for line in startup.splitlines() if "docker run" in line)
        assert "-e GLIBC_TUNABLES=glibc.pthread.rseq=1" in docker_run
