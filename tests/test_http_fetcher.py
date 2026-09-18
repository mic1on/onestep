from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from onestep import HttpFetcher
from onestep.config import load_app_config
from onestep.resilience import ConnectorErrorKind, ConnectorOperationError

_JSON_BODY = b'{"code": 200, "rows": [{"id": 1}, {"id": 2}]}'


def _extract_rows(payload: Any) -> list[dict[str, Any]]:
    return payload["rows"]


async def _extract_rows_async(payload: Any) -> list[dict[str, Any]]:
    return payload["rows"]


async def _sync_handler(ctx: Any, payload: Any) -> None:
    rows = await ctx.resources["api"].fetch_rows()
    for row in rows:
        await ctx.emit(row)


async def _start_json_server(
    *,
    status: int = 200,
    body: bytes = _JSON_BODY,
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


def test_fetch_returns_parsed_json_with_query_params_and_accept_header() -> None:
    async def scenario() -> None:
        server, requests, base_url = await _start_json_server()
        try:
            fetcher = HttpFetcher(
                "api",
                url=f"{base_url}/list",
                params={"pageSize": 10},
                timeout_s=1.0,
            )
            payload = await fetcher.fetch()
        finally:
            await _close_server(server)

        assert len(requests) == 1
        request = requests[0]
        assert request["method"] == "GET"
        assert request["target"] == "/list?pageSize=10"
        assert request["headers"]["accept"] == "application/json"
        assert payload == {"code": 200, "rows": [{"id": 1}, {"id": 2}]}

    asyncio.run(scenario())


def test_fetch_params_override_merges_over_static_params() -> None:
    async def scenario() -> None:
        server, requests, base_url = await _start_json_server()
        try:
            fetcher = HttpFetcher(
                "api",
                url=f"{base_url}/list",
                params={"pageSize": 10, "page": 1},
                timeout_s=1.0,
            )
            await fetcher.fetch(params_override={"page": 2})
        finally:
            await _close_server(server)

        assert len(requests) == 1
        assert requests[0]["target"] == "/list?pageSize=10&page=2"

    asyncio.run(scenario())


def test_fetch_raises_fetch_failure_for_non_success_status() -> None:
    async def scenario() -> None:
        server, requests, base_url = await _start_json_server(status=500, body=b"failed")
        try:
            fetcher = HttpFetcher("api", url=f"{base_url}/list", timeout_s=1.0)
            with pytest.raises(ConnectorOperationError) as raised:
                await fetcher.fetch()
        finally:
            await _close_server(server)

        assert len(requests) == 1
        assert raised.value.backend == "http_fetcher"
        assert raised.value.kind is ConnectorErrorKind.TRANSIENT

    asyncio.run(scenario())


def test_fetch_raises_fetch_failure_for_non_json_body() -> None:
    async def scenario() -> None:
        server, requests, base_url = await _start_json_server(status=200, body=b"ok")
        try:
            fetcher = HttpFetcher("api", url=f"{base_url}/list", timeout_s=1.0)
            with pytest.raises(ConnectorOperationError) as raised:
                await fetcher.fetch()
        finally:
            await _close_server(server)

        assert raised.value.backend == "http_fetcher"
        assert raised.value.kind is ConnectorErrorKind.PERMANENT

    asyncio.run(scenario())


def test_fetch_raises_fetch_failure_for_transport_error() -> None:
    async def scenario() -> None:
        fetcher = HttpFetcher("api", url="http://127.0.0.1:1/list", timeout_s=0.5)
        with pytest.raises(ConnectorOperationError) as raised:
            await fetcher.fetch()

        assert raised.value.backend == "http_fetcher"
        assert raised.value.kind is ConnectorErrorKind.DISCONNECTED

    asyncio.run(scenario())


def test_fetch_rows_applies_sync_extractor() -> None:
    async def scenario() -> None:
        server, _requests, base_url = await _start_json_server()
        try:
            fetcher = HttpFetcher(
                "api",
                url=f"{base_url}/list",
                timeout_s=1.0,
                rows=_extract_rows,
            )
            rows = await fetcher.fetch_rows()
        finally:
            await _close_server(server)

        assert rows == [{"id": 1}, {"id": 2}]

    asyncio.run(scenario())


def test_fetch_rows_supports_async_extractor() -> None:
    async def scenario() -> None:
        server, _requests, base_url = await _start_json_server()
        try:
            fetcher = HttpFetcher(
                "api",
                url=f"{base_url}/list",
                timeout_s=1.0,
                rows=_extract_rows_async,
            )
            rows = await fetcher.fetch_rows()
        finally:
            await _close_server(server)

        assert rows == [{"id": 1}, {"id": 2}]

    asyncio.run(scenario())


def test_fetch_rows_without_extractor_is_misconfigured() -> None:
    async def scenario() -> None:
        fetcher = HttpFetcher("api", url="https://example.com/list", timeout_s=1.0)
        with pytest.raises(ConnectorOperationError) as raised:
            await fetcher.fetch_rows()

        assert raised.value.kind is ConnectorErrorKind.MISCONFIGURED

    asyncio.run(scenario())


def test_fetch_rows_rejects_non_list_extractor_result() -> None:
    async def scenario() -> None:
        server, _requests, base_url = await _start_json_server()
        try:
            fetcher = HttpFetcher(
                "api",
                url=f"{base_url}/list",
                timeout_s=1.0,
                rows=lambda payload: {"not": "a list"},
            )
            with pytest.raises(TypeError, match="must return a list"):
                await fetcher.fetch_rows()
        finally:
            await _close_server(server)

    asyncio.run(scenario())


def test_fetch_rows_rejects_non_mapping_extractor_item() -> None:
    async def scenario() -> None:
        server, _requests, base_url = await _start_json_server()
        try:
            fetcher = HttpFetcher(
                "api",
                url=f"{base_url}/list",
                timeout_s=1.0,
                rows=lambda payload: [1, 2],
            )
            with pytest.raises(TypeError, match="must be a mapping"):
                await fetcher.fetch_rows()
        finally:
            await _close_server(server)

    asyncio.run(scenario())


def test_yaml_http_fetcher_resource_wiring() -> None:
    async def scenario() -> None:
        server, requests, base_url = await _start_json_server()
        try:
            app = load_app_config(
                {
                    "apiVersion": "onestep/v1alpha1",
                    "kind": "App",
                    "app": {"name": "yaml-http-fetcher"},
                    "resources": {
                        "api": {
                            "type": "http_fetcher",
                            "url": f"{base_url}/list",
                            "params": {"pageSize": 2},
                            "rows": f"{__name__}:_extract_rows",
                        },
                    },
                    "tasks": [],
                },
                strict=True,
            )

            fetcher = app.resources["api"]
            assert isinstance(fetcher, HttpFetcher)
            assert fetcher.method == "GET"
            assert fetcher.timeout_s == 5.0

            rows = await fetcher.fetch_rows()
            assert rows == [{"id": 1}, {"id": 2}]
            assert requests[0]["target"] == "/list?pageSize=2"
        finally:
            await _close_server(server)

    asyncio.run(scenario())


def test_yaml_fetcher_used_from_handler_via_ctx_resources_and_emit() -> None:
    async def scenario() -> None:
        server, requests, base_url = await _start_json_server()
        try:
            app = load_app_config(
                {
                    "apiVersion": "onestep/v1alpha1",
                    "kind": "App",
                    "app": {"name": "yaml-fetcher-handler"},
                    "resources": {
                        "incoming": {"type": "memory", "maxsize": 100},
                        "outgoing": {"type": "memory", "maxsize": 100},
                        "api": {
                            "type": "http_fetcher",
                            "url": f"{base_url}/list",
                            "rows": f"{__name__}:_extract_rows",
                        },
                    },
                    "tasks": [
                        {
                            "name": "sync",
                            "source": "incoming",
                            "emit": "outgoing",
                            "handler": {"ref": f"{__name__}:_sync_handler"},
                        }
                    ],
                },
                strict=True,
            )

            await app.startup()
            try:
                result = await app.run_task_once("sync", payload={})
                emitted = app.resources["outgoing"].size()
            finally:
                await app.shutdown()
        finally:
            await _close_server(server)

        assert result["completion"] == "complete"
        assert emitted == 2
        assert len(requests) == 1

    asyncio.run(scenario())


def test_strict_yaml_rejects_unknown_http_fetcher_fields() -> None:
    with pytest.raises(ValueError, match="unsupported fields for resources.api: token"):
        load_app_config(
            {
                "apiVersion": "onestep/v1alpha1",
                "kind": "App",
                "app": {"name": "yaml-invalid-fetcher"},
                "resources": {
                    "api": {
                        "type": "http_fetcher",
                        "url": "https://example.com/list",
                        "token": "secret-token",
                    },
                },
                "tasks": [],
            },
            strict=True,
        )


def test_strict_yaml_rejects_non_string_rows_ref() -> None:
    with pytest.raises(TypeError, match="rows"):
        load_app_config(
            {
                "apiVersion": "onestep/v1alpha1",
                "kind": "App",
                "app": {"name": "yaml-invalid-fetcher-rows"},
                "resources": {
                    "api": {
                        "type": "http_fetcher",
                        "url": "https://example.com/list",
                        "rows": 123,
                    },
                },
                "tasks": [],
            },
            strict=True,
        )


def test_connectors_namespace_exports_http_fetcher() -> None:
    import onestep.connectors as connectors

    assert connectors.HttpFetcher is HttpFetcher
    assert "HttpFetcher" in connectors.__all__


def test_fetch_post_sends_static_json_body_with_content_type() -> None:
    async def scenario() -> None:
        server, requests, base_url = await _start_json_server()
        try:
            fetcher = HttpFetcher(
                "api",
                url=f"{base_url}/search",
                method="POST",
                params={"pageSize": 10},
                body={"page": 1, "keyword": "abc"},
                timeout_s=1.0,
            )
            payload = await fetcher.fetch()
        finally:
            await _close_server(server)

        request = requests[0]
        assert request["method"] == "POST"
        assert request["target"] == "/search?pageSize=10"
        assert request["headers"]["accept"] == "application/json"
        assert request["headers"]["content-type"] == "application/json"
        assert json.loads(request["body"]) == {"page": 1, "keyword": "abc"}
        assert payload == {"code": 200, "rows": [{"id": 1}, {"id": 2}]}

    asyncio.run(scenario())


def test_fetch_body_override_replaces_static_body() -> None:
    async def scenario() -> None:
        server, requests, base_url = await _start_json_server()
        try:
            fetcher = HttpFetcher(
                "api",
                url=f"{base_url}/search",
                method="POST",
                body={"page": 1},
                rows=_extract_rows,
                timeout_s=1.0,
            )
            await fetcher.fetch(body_override={"page": 2, "keyword": "x"})
            rows = await fetcher.fetch_rows(body_override={"page": 3})
        finally:
            await _close_server(server)

        assert rows == [{"id": 1}, {"id": 2}]
        assert json.loads(requests[0]["body"]) == {"page": 2, "keyword": "x"}
        assert json.loads(requests[1]["body"]) == {"page": 3}

    asyncio.run(scenario())


def test_fetch_bodyless_method_ignores_configured_body() -> None:
    async def scenario() -> None:
        server, requests, base_url = await _start_json_server()
        try:
            fetcher = HttpFetcher(
                "api",
                url=f"{base_url}/list",
                method="GET",
                body={"page": 1},
                timeout_s=1.0,
            )
            await fetcher.fetch()
        finally:
            await _close_server(server)

        request = requests[0]
        assert request["method"] == "GET"
        assert request["body"] == b""
        assert "content-type" not in request["headers"]

    asyncio.run(scenario())


def test_yaml_http_fetcher_post_body_wiring() -> None:
    async def scenario() -> None:
        server, requests, base_url = await _start_json_server()
        try:
            app = load_app_config(
                {
                    "apiVersion": "onestep/v1alpha1",
                    "kind": "App",
                    "app": {"name": "yaml-http-fetcher-body"},
                    "resources": {
                        "api": {
                            "type": "http_fetcher",
                            "url": f"{base_url}/search",
                            "method": "POST",
                            "params": {"pageSize": 2},
                            "body": {"page": 1},
                            "rows": f"{__name__}:_extract_rows",
                        },
                    },
                    "tasks": [],
                },
                strict=True,
            )

            fetcher = app.resources["api"]
            assert isinstance(fetcher, HttpFetcher)
            rows = await fetcher.fetch_rows()
            assert rows == [{"id": 1}, {"id": 2}]
            request = requests[0]
            assert request["method"] == "POST"
            assert request["target"] == "/search?pageSize=2"
            assert request["headers"]["content-type"] == "application/json"
            assert json.loads(request["body"]) == {"page": 1}
        finally:
            await _close_server(server)

    asyncio.run(scenario())
