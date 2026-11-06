from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/run-integration-tests.sh"


@pytest.mark.parametrize(
    ("setup_status", "pytest_status", "keep_services", "diagnostic_status"),
    [
        (42, 0, False, 9),
        (42, 0, True, 0),
        (0, 7, False, 0),
        (0, 0, False, 0),
        (0, 0, True, 0),
    ],
)
def test_integration_runner_preserves_failures_and_cleans_up(
    tmp_path: Path,
    setup_status: int,
    pytest_status: int,
    keep_services: bool,
    diagnostic_status: int,
) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    runner = scripts / SCRIPT.name
    shutil.copyfile(SCRIPT, runner)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    trace = tmp_path / "trace"
    integration_path = "plugins/onestep-kafka/tests/integration"
    (tmp_path / integration_path).mkdir(parents=True)
    (tmp_path / integration_path / "test_example.py").touch()

    programs = {
        scripts / "setup-integration-env.sh": '''#!/usr/bin/env bash
echo setup >> "$TRACE"
echo 'export ONESTEP_KAFKA_BOOTSTRAP_SERVERS=127.0.0.1:19092'
if [[ "$SETUP_STATUS" != 0 ]]; then
  echo 'MongoDB setup failed' >&2
fi
exit "$SETUP_STATUS"
''',
        bin_dir / "docker": '''#!/usr/bin/env bash
if [[ "$1" == info ]]; then
  echo kernel >> "$TRACE"
  exit 0
fi
# compose -f <file> <operation>
echo "$4" >> "$TRACE"
case "$4" in
  ps|logs)
    echo "diagnostic $4" >&2
    exit "$DIAGNOSTIC_STATUS"
    ;;
esac
''',
        bin_dir / "python": '''#!/usr/bin/env bash
echo "pytest $* bootstrap=$ONESTEP_KAFKA_BOOTSTRAP_SERVERS" >> "$TRACE"
exit "$PYTEST_STATUS"
''',
    }
    for path, source in programs.items():
        path.write_text(source, encoding="utf-8")
        path.chmod(0o755)

    result = subprocess.run(
        ["bash", str(runner), "-k", "example"],
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "ONESTEP_PYTHON_BIN": str(bin_dir / "python"),
            "KEEP_INTEGRATION_SERVICES": str(int(keep_services)),
            "TRACE": str(trace),
            "SETUP_STATUS": str(setup_status),
            "PYTEST_STATUS": str(pytest_status),
            "DIAGNOSTIC_STATUS": str(diagnostic_status),
        },
        capture_output=True,
        text=True,
        timeout=10,
    )

    expected_status = setup_status or pytest_status
    assert result.returncode == expected_status, result.stderr
    expected_calls = ["kernel", "up", "setup"]
    if setup_status:
        assert "MongoDB setup failed" in result.stderr
    else:
        expected_calls.append(
            f"pytest -m pytest {integration_path} -q -k example bootstrap=127.0.0.1:19092"
        )
    if expected_status:
        expected_calls.extend(["ps", "logs"])
        assert "diagnostic ps" in result.stderr
        assert "diagnostic logs" in result.stderr
    else:
        assert result.stderr == ""
    if not keep_services:
        expected_calls.append("down")
    assert trace.read_text(encoding="utf-8").splitlines() == expected_calls
