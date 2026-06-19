from __future__ import annotations

import io
import zipfile

import yaml
from onestep_control_plane_api.api.worker_compiler import (
    compile_worker_yaml,
    merge_package,
)


def _worker(
    name="order-sync",
    handler_ref="myworker.handlers:sync",
    source=None,
    sinks=None,
    reporting_enabled=True,
    reporting_config=None,
):
    return {
        "name": name,
        "handler_ref": handler_ref,
        "source": source or {
            "type": "mysql_incremental",
            "connector_id": "c1",
            "fields": {"table": "orders", "key": "id", "cursor": ["updated_at", "id"]},
        },
        "sinks": sinks or [
            {
                "type": "mysql_table_sink",
                "connector_id": "c1",
                "fields": {"table": "synced", "mode": "upsert", "keys": ["id"]},
            }
        ],
        "reporting_enabled": reporting_enabled,
        "reporting_config": reporting_config or {"mode": "platform", "endpoint_url": None},
    }


def _connectors():
    return {
        "c1": {"type": "mysql", "config": {}, "secret": {"dsn": "mysql://user:pass@host/db"}},
    }


def test_compile_single_source_single_sink():
    yml = compile_worker_yaml(_worker(), _connectors())
    data = yaml.safe_load(yml)
    assert data["app"]["name"] == "order-sync"
    assert data["apiVersion"] == "onestep/v1alpha1"
    assert data["kind"] == "App"
    # One connector resource (shared by source + sink).
    conn_resources = {k: v for k, v in data["resources"].items() if v["type"] == "mysql"}
    assert len(conn_resources) == 1
    conn = list(conn_resources.values())[0]
    assert conn["dsn"] == "mysql://user:pass@host/db"
    # Source resource references the connector.
    src = [v for v in data["resources"].values() if v["type"] == "mysql_incremental"][0]
    assert src["connector"] in conn_resources
    assert src["table"] == "orders"
    # Task: single source, emit list with one sink.
    task = data["tasks"][0]
    assert task["name"] == "main"
    assert task["source"] in data["resources"]
    assert task["emit"] == [
        k
        for k in data["resources"]
        if data["resources"][k]["type"] == "mysql_table_sink"
    ]
    assert task["handler"]["ref"] == "myworker.handlers:sync"
    assert data["reporter"] is True


def test_compile_multiple_sinks_uses_emit_list():
    worker = _worker(
        sinks=[
            {"type": "mysql_table_sink", "connector_id": "c1", "fields": {"table": "t1"}},
            {"type": "mysql_table_sink", "connector_id": "c1", "fields": {"table": "t2"}},
        ]
    )
    yml = compile_worker_yaml(worker, _connectors())
    data = yaml.safe_load(yml)
    task = data["tasks"][0]
    assert len(task["emit"]) == 2


def test_compile_builtin_source_has_no_connector_key():
    worker = _worker(
        source={
            "type": "interval",
            "connector_id": None,
            "fields": {"seconds": 1},
        },
        sinks=[
            {"type": "http_sink", "connector_id": None, "fields": {"url": "https://out.example.com"}},
        ],
    )
    yml = compile_worker_yaml(worker, {})
    data = yaml.safe_load(yml)
    assert data["reporter"] is True
    src = [v for v in data["resources"].values() if v["type"] == "interval"][0]
    assert "connector" not in src
    assert src["seconds"] == 1


def test_compile_omits_reporter_when_reporting_disabled():
    yml = compile_worker_yaml(_worker(reporting_enabled=False), _connectors())
    data = yaml.safe_load(yml)
    assert "reporter" not in data


def test_compile_custom_reporting_uses_token_env_placeholder():
    yml = compile_worker_yaml(
        _worker(
            reporting_config={
                "mode": "custom",
                "endpoint_url": "https://telemetry.example.com",
            }
        ),
        _connectors(),
    )
    data = yaml.safe_load(yml)
    assert data["reporter"] == {
        "base_url": "https://telemetry.example.com",
        "token": "${ONESTEP_WORKER_REPORTING_TOKEN}",
    }


def test_compile_shared_connector_dedup():
    # Source and sink reference the same connector_id — one connector resource.
    yml = compile_worker_yaml(_worker(), _connectors())
    data = yaml.safe_load(yml)
    conn_keys = [k for k, v in data["resources"].items() if v["type"] == "mysql"]
    assert len(conn_keys) == 1


def test_merge_package_adds_worker_yaml():
    # Handler zip with code but no worker.yaml.
    handler_buf = io.BytesIO()
    with zipfile.ZipFile(handler_buf, "w") as zf:
        zf.writestr("src/myworker/handlers.py", "def sync(): pass")
    handler_bytes = handler_buf.getvalue()

    merged = merge_package(handler_bytes, "app:\n  name: test\n")
    with zipfile.ZipFile(io.BytesIO(merged), "r") as zf:
        names = zf.namelist()
    assert "worker.yaml" in names
    assert "src/myworker/handlers.py" in names


def test_merge_package_overwrites_existing_worker_yaml():
    handler_buf = io.BytesIO()
    with zipfile.ZipFile(handler_buf, "w") as zf:
        zf.writestr("worker.yaml", "OLD")
        zf.writestr("code.py", "x = 1")
    handler_bytes = handler_buf.getvalue()

    merged = merge_package(handler_bytes, "app:\n  name: new\n")
    with zipfile.ZipFile(io.BytesIO(merged), "r") as zf:
        assert zf.read("worker.yaml").decode() == "app:\n  name: new\n"
        assert zf.read("code.py").decode() == "x = 1"
