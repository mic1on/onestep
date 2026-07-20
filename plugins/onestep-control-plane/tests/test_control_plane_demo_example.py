from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


def _load_demo_module(monkeypatch):
    monkeypatch.setenv("ONESTEP_CONTROL_PLANE_URL", "http://127.0.0.1:8080")
    monkeypatch.setenv("ONESTEP_CONTROL_PLANE_TOKEN", "dev-token")
    module_name = "_test_control_plane_reporter_demo"
    module_path = Path(__file__).resolve().parents[3] / "example" / "control_plane_reporter_demo.py"
    sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _load_mysql_demo_module(monkeypatch):
    monkeypatch.setenv("CP_MYSQL_DEMO_START_MYSQL", "0")
    monkeypatch.setenv("CP_MYSQL_DEMO_INTERVAL_S", "5")
    monkeypatch.setenv("CP_MYSQL_DEMO_TABLE", "cp_demo_events")
    monkeypatch.setenv("MYSQL_DSN", "mysql+pymysql://root:root@127.0.0.1:3306/app")
    monkeypatch.delenv("ONESTEP_CONTROL_PLANE_URL", raising=False)
    monkeypatch.delenv("ONESTEP_CONTROL_PLANE_TOKEN", raising=False)

    module_name = "_test_control_plane_mysql_demo"
    module_path = Path(__file__).resolve().parents[3] / "example" / "control_plane_mysql_demo.py"
    sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _read_workspace_version() -> str:
    pyproject_path = Path(__file__).resolve().parents[3] / "pyproject.toml"
    for line in pyproject_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("version = "):
            return line.split('"', 2)[1]
    raise AssertionError("did not find project version in pyproject.toml")


def test_demo_job_cycle_is_predictable(monkeypatch) -> None:
    module = _load_demo_module(monkeypatch)

    assert module._build_demo_job(1)["behavior"] == "ok"
    assert module._build_demo_job(2)["behavior"] == "retry_once"
    assert module._build_demo_job(3)["behavior"] == "fail"
    assert module._build_demo_job(4)["behavior"] == "slow"
    assert module._build_demo_job(5)["behavior"] == "ok"


def test_demo_control_plane_urls_are_console_friendly(monkeypatch) -> None:
    module = _load_demo_module(monkeypatch)

    urls = module._build_control_plane_urls(
        base_url="http://127.0.0.1:8080",
        service_name="demo service",
        environment="dev",
        instance_id="1234",
    )

    assert urls["dashboard"] == "http://127.0.0.1:8080/services/demo%20service?environment=dev"
    assert urls["commands"].endswith("/services/demo%20service?environment=dev&tab=commands")
    assert urls["instance_detail"] == (
        "http://127.0.0.1:8080/services/demo%20service/instances/1234"
        "?environment=dev&lookback_minutes=60"
    )


def test_mysql_demo_app_target_uses_workspace_onestep_version(monkeypatch) -> None:
    module = _load_mysql_demo_module(monkeypatch)

    assert module.ONESTEP_VERSION == _read_workspace_version()

    summary = module.app.describe()
    assert summary["name"] == "cp-mysql-demo"
    assert summary["hooks"] == {"startup": 2, "shutdown": 0, "events": 0}
    assert len(summary["tasks"]) == 1

    task = summary["tasks"][0]
    assert task["name"] == "produce_and_store"
    assert task["source"] == {"name": "interval:5s", "type": "IntervalSource"}
    assert task["emit"] == [{"name": "mysql.table_sink:cp_demo_events", "type": "TableSink"}]
    assert task["retry"] == "MaxAttempts"
