from __future__ import annotations

import asyncio
import json
from typing import Optional

import pytest

from onestep.app import OneStepApp
from onestep.events import TaskEvent, TaskEventKind
from onestep.observability import MetricsServer, PrometheusExporter, install_metrics


async def _http_get(port: int, path: str) -> tuple[int, str, str]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    request = f"GET {path} HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
    writer.write(request.encode("iso-8859-1"))
    await writer.drain()
    raw = await reader.read(-1)
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass
    head, _, body = raw.partition(b"\r\n\r\n")
    head_lines = head.decode("iso-8859-1").split("\r\n")
    status = int(head_lines[0].split()[1])
    headers: dict[str, str] = {}
    for line in head_lines[1:]:
        key, _, value = line.partition(":")
        headers[key.strip().lower()] = value.strip()
    return status, headers.get("content-type", ""), body.decode("utf-8", errors="replace")


def make_event(
    kind: TaskEventKind,
    *,
    app: str = "e2e",
    task: str = "sync",
    duration_s: Optional[float] = None,
) -> TaskEvent:
    return TaskEvent(
        kind=kind,
        app=app,
        task=task,
        source="incoming",
        attempts=0,
        duration_s=duration_s,
    )


@pytest.mark.asyncio
async def test_metrics_server_serves_exporter_payload() -> None:
    exporter = PrometheusExporter(app_name="solo")
    server = MetricsServer(
        exporter,
        host="127.0.0.1",
        port=0,
        health_provider=lambda: {"status": "ok"},
    )

    await server.start()
    try:
        status, content_type, body = await _http_get(server.bound_port, "/metrics")
        assert status == 200
        assert content_type.startswith("text/plain")
        assert "onestep_build_info" in body
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_install_metrics_wires_events_and_http_together() -> None:
    app = OneStepApp("metrics-e2e")
    handle = install_metrics(app, host="127.0.0.1", port=0)

    await app.startup()
    try:
        status, _, body = await _http_get(handle.bound_port, "/metrics")
        assert status == 200
        assert "tasks" not in body or 'onestep_tasks_processed_total' in body

        await app.emit_event(make_event(TaskEventKind.SUCCEEDED, duration_s=0.2))

        status, _, body = await _http_get(handle.bound_port, "/metrics")
        assert status == 200
        assert (
            'onestep_tasks_processed_total{app="e2e",task="sync",status="succeeded"} 1'
            in body.splitlines()
        )
    finally:
        await app.shutdown()


@pytest.mark.asyncio
async def test_healthz_reports_runtime_and_sources() -> None:
    app = OneStepApp("healthz-e2e")
    handle = install_metrics(app, host="127.0.0.1", port=0)

    await app.startup()
    try:
        status, content_type, body = await _http_get(handle.bound_port, "/healthz")
        assert status == 200
        assert content_type.startswith("application/json")
        payload = json.loads(body)
        assert payload["status"] == "ok"
        assert payload["app"] == "healthz-e2e"
        assert isinstance(payload["tasks"], list)
    finally:
        await app.shutdown()


@pytest.mark.asyncio
async def test_unknown_path_returns_404() -> None:
    exporter = PrometheusExporter(app_name="solo")
    server = MetricsServer(exporter, host="127.0.0.1", port=0)
    await server.start()
    try:
        status, _, _ = await _http_get(server.bound_port, "/nope")
        assert status == 404
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_server_stop_releases_port() -> None:
    exporter = PrometheusExporter(app_name="solo")
    server = MetricsServer(exporter, host="127.0.0.1", port=0)
    await server.start()
    port = server.bound_port
    await server.stop()

    next_server = MetricsServer(exporter, host="127.0.0.1", port=port)
    await next_server.start()
    try:
        assert next_server.bound_port == port
    finally:
        await next_server.stop()


def test_run_accepts_metrics_addr_flag() -> None:
    from onestep.cli import parse_args

    args = parse_args(["run", "app.yaml", "--metrics-addr", ":9100"])
    assert args.metrics_addr == ":9100"

    args_default = parse_args(["run", "app.yaml"])
    assert args_default.metrics_addr is None
