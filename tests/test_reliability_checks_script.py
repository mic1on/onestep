from __future__ import annotations

import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run-reliability-checks.sh"


def test_reliability_check_script_is_executable_and_valid_bash() -> None:
    assert SCRIPT.exists()
    assert os.access(SCRIPT, os.X_OK)
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def test_reliability_check_script_runs_plugin_suites_in_isolated_processes() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert '"$PYTHON_BIN" -m pytest -q -m "not integration" tests "$@"' in text
    for plugin in (
        "plugins/onestep-feishu-bitable/tests",
        "plugins/onestep-mysql/tests",
        "plugins/onestep-postgres/tests",
        "plugins/onestep-rabbitmq/tests",
        "plugins/onestep-redis/tests",
        "plugins/onestep-sqs/tests",
    ):
        assert plugin in text
    assert 'for path in "${plugin_paths[@]}"' in text
    assert '"$PYTHON_BIN" -m pytest -q -m "not integration" "$path" "$@"' in text
