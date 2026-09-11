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
        "plugins/onestep-clickhouse/tests",
        "plugins/onestep-control-plane/tests",
        "plugins/onestep-elasticsearch/tests",
        "plugins/onestep-feishu-bitable/tests",
        "plugins/onestep-mongodb/tests",
        "plugins/onestep-mysql/tests",
        "plugins/onestep-postgres/tests",
        "plugins/onestep-rabbitmq/tests",
        "plugins/onestep-redis/tests",
        "plugins/onestep-sqs/tests",
        "plugins/onestep-cf-queues/tests",
    ):
        assert plugin in text
    assert 'for entry in "${plugin_tests[@]}"' in text
    assert '"$PYTHON_BIN" -m pytest -q -m "not integration" "$path" "$@"' in text


def test_reliability_check_script_skips_plugins_with_missing_packages() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    # A partially-synced .venv must skip the plugin suite instead of aborting
    # the whole run with collection errors.
    assert 'if ! "$PYTHON_BIN" -c "import $package" >/dev/null 2>&1; then' in text
    assert "not importable" in text
