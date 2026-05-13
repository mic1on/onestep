from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from control_plane_testkit import SenderRecorder, make_config
from onestep import ControlPlaneReporter, Envelope, HttpSink, MemoryQueue, OneStepApp
from onestep.config import load_app_config
from onestep.connectors.http import HttpSinkStatusError
from onestep.resilience import ConnectorErrorKind, ConnectorOperationError


async def _start_http_server(
    *,
    status: int = 202,
    body: bytes = b"ok",
) -> tuple[asyncio.AbstractServer, list[dict[str, Any]], str]:
    requests: list[dict[str, Any]] = []

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        request = await _read_request(reader)
        requests.append(request)
        response = (
            f"HTTP/1.1 {status} {_reason(status)}\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n"
            "\r\n"
        ).encode("ascii") + body
        writer.write(response)
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    assert server.sockets is not None
    host, port = server.sockets[0].getsockname()[:2]
    return server, requests, f"http://{host}:{port}"


async def _read_request(reader: asyncio.StreamReader) -> dict[str, Any]:
    header_block = await reader.readuntil(b"\r\n\r\n")
    header_text = header_block.decode("iso-8859-1")
    lines = header_text.split("\r\n")
    method, target, _ = lines[0].split(" ", 2)
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if not line:
            continue
        name, _, value = line.partition(":")
        headers[name.lower()] = value.strip()
    content_length = int(headers.get("content-length", "0"))
    body = await reader.readexactly(content_length) if content_length else b""
    return {
        "method": method,
        "target": target,
        "headers": headers,
        "body": body,
    }


def _reason(status: int) -> str:
    return {
        200: "OK",
        202: "Accepted",
        500: "Internal Server Error",
    }.get(status, "Status")


async def _close_server(server: asyncio.AbstractServer) -> None:
    server.close()
    await server.wait_closed()


def test_http_sink_sends_json_body_and_headers() -> None:
    async def scenario() -> None:
        server, requests, base_url = await _start_http_server(status=202)
        try:
            sink = HttpSink(
                "notify",
                url=f"{base_url}/events",
                headers={"Authorization": "Bearer secret-token"},
                timeout_s=1.0,
            )
            await sink.open()
            await sink.send(Envelope(body={"id": 123, "kind": "order"}))
            await sink.close()
        finally:
            await _close_server(server)

        assert len(requests) == 1
        request = requests[0]
        assert request["method"] == "POST"
        assert request["target"] == "/events"
        assert request["headers"]["authorization"] == "Bearer secret-token"
        assert request["headers"]["content-type"] == "application/json"
        assert json.loads(request["body"].decode("utf-8")) == {
            "id": 123,
            "kind": "order",
        }

    asyncio.run(scenario())


def test_http_sink_raises_send_failure_for_non_success_status() -> None:
    async def scenario() -> None:
        server, requests, base_url = await _start_http_server(status=500, body=b"failed")
        try:
            sink = HttpSink("notify", url=f"{base_url}/events", timeout_s=1.0)
            with pytest.raises(ConnectorOperationError) as raised:
                await sink.send(Envelope(body={"id": 123}))
        finally:
            await _close_server(server)

        assert len(requests) == 1
        assert raised.value.backend == "http_sink"
        assert raised.value.kind is ConnectorErrorKind.TRANSIENT
        assert isinstance(raised.value.cause, HttpSinkStatusError)
        assert raised.value.cause.status == 500

    asyncio.run(scenario())


def test_yaml_http_sink_and_passthrough_task_emit_payload() -> None:
    async def scenario() -> None:
        server, requests, base_url = await _start_http_server(status=202)
        try:
            app = load_app_config(
                {
                    "apiVersion": "onestep/v1alpha1",
                    "kind": "App",
                    "app": {"name": "yaml-http"},
                    "resources": {
                        "incoming": {"type": "memory"},
                        "notify": {
                            "type": "http_sink",
                            "url": f"{base_url}/notify",
                            "headers": {"X-Api-Key": "secret-token"},
                        },
                    },
                    "tasks": [
                        {
                            "name": "forward",
                            "source": "incoming",
                            "emit": "notify",
                        }
                    ],
                },
                strict=True,
            )
            assert app.tasks[0].handler_ref is None
            assert isinstance(app.resources["notify"], HttpSink)

            await app.startup()
            try:
                result = await app.run_task_once("forward", payload={"value": 7})
            finally:
                await app.shutdown()
        finally:
            await _close_server(server)

        assert result["completion"] == "complete"
        assert len(requests) == 1
        assert json.loads(requests[0]["body"].decode("utf-8")) == {"value": 7}

    asyncio.run(scenario())


def test_strict_yaml_rejects_passthrough_task_without_emit() -> None:
    with pytest.raises(ValueError, match=r"tasks\[0\] must define either 'handler' or 'emit'"):
        load_app_config(
            {
                "apiVersion": "onestep/v1alpha1",
                "kind": "App",
                "app": {"name": "yaml-invalid"},
                "resources": {
                    "incoming": {"type": "memory"},
                },
                "tasks": [
                    {
                        "name": "missing",
                        "source": "incoming",
                    }
                ],
            },
            strict=True,
        )


def test_strict_yaml_rejects_unknown_http_sink_fields() -> None:
    with pytest.raises(ValueError, match="unsupported fields for resources.notify: token"):
        load_app_config(
            {
                "apiVersion": "onestep/v1alpha1",
                "kind": "App",
                "app": {"name": "yaml-invalid-http"},
                "resources": {
                    "notify": {
                        "type": "http_sink",
                        "url": "https://example.com/notify",
                        "token": "secret-token",
                    },
                },
                "tasks": [],
            },
            strict=True,
        )


def test_reporter_describes_http_sink_kind_and_redacts_config() -> None:
    recorder = SenderRecorder()
    app = OneStepApp("billing-sync")
    sink = HttpSink(
        "notify",
        url="https://user:password@example.com/hooks?token=secret-token&safe=1#fragment",
        headers={
            "Authorization": "Bearer secret-token",
            "X-Api-Key": "secret-token",
        },
        timeout_s=2.5,
        success_statuses=[202],
    )

    @app.task(source=MemoryQueue("incoming"), emit=sink)
    async def forward(ctx, payload):
        return payload

    reporter = ControlPlaneReporter(make_config(), sender=recorder)
    reporter.attach(app)

    async def scenario() -> None:
        await reporter.send_sync_now()

    asyncio.run(scenario())

    sync_payload = next(payload for channel, payload in recorder.calls if channel == "sync")
    emit = sync_payload["app"]["tasks"][0]["emit"][0]
    assert emit == {
        "kind": "http_sink",
        "name": "notify",
        "config": {
            "url": "https://<redacted>@example.com/hooks",
            "method": "POST",
            "headers": {
                "Authorization": "<redacted>",
                "X-Api-Key": "<redacted>",
            },
            "timeout_s": 2.5,
            "success_statuses": [202],
        },
    }
    assert "secret-token" not in json.dumps(sync_payload["app"])
