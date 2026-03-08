import asyncio
import socket

from onestep import BearerAuth, MemoryQueue, OneStepApp, WebhookSource


def _reserve_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return int(port)


async def _http_request(host: str, port: int, request: bytes) -> bytes:
    reader, writer = await asyncio.open_connection(host, port)
    writer.write(request)
    await writer.drain()
    response = await reader.read()
    writer.close()
    await writer.wait_closed()
    return response


def test_webhook_source_ingests_json_request() -> None:
    async def scenario() -> None:
        port = _reserve_port()
        source = WebhookSource(path="/hooks/orders", host="127.0.0.1", port=port)
        sink = MemoryQueue("processed")
        app = OneStepApp("webhook-json")

        @app.task(source=source, emit=sink)
        async def ingest(ctx, event):
            ctx.app.request_shutdown()
            return {
                "kind": event["body"]["type"],
                "query": event["query"]["source"],
                "method": event["method"],
                "path": event["path"],
            }

        app_task = asyncio.create_task(app.serve())
        await asyncio.sleep(0.05)

        response = await _http_request(
            "127.0.0.1",
            port,
            (
                b"POST /hooks/orders?source=crm HTTP/1.1\r\n"
                b"Host: 127.0.0.1\r\n"
                b"Content-Type: application/json\r\n"
                b"Content-Length: 16\r\n"
                b"\r\n"
                b'{"type":"order"}'
            ),
        )
        assert response.startswith(b"HTTP/1.1 202 Accepted")

        await asyncio.wait_for(app_task, timeout=1.0)
        deliveries = await sink.fetch(1)
        assert len(deliveries) == 1
        assert deliveries[0].payload == {
            "kind": "order",
            "query": "crm",
            "method": "POST",
            "path": "/hooks/orders",
        }

    asyncio.run(scenario())


def test_webhook_source_shares_server_across_routes() -> None:
    async def scenario() -> None:
        port = _reserve_port()
        left = WebhookSource(path="/left", host="127.0.0.1", port=port)
        right = WebhookSource(path="/right", host="127.0.0.1", port=port, methods=("POST", "PUT"))
        await left.open()
        await right.open()

        left_response = await _http_request(
            "127.0.0.1",
            port,
            (
                b"POST /left HTTP/1.1\r\n"
                b"Host: 127.0.0.1\r\n"
                b"Content-Length: 0\r\n"
                b"\r\n"
            ),
        )
        right_response = await _http_request(
            "127.0.0.1",
            port,
            (
                b"PUT /right HTTP/1.1\r\n"
                b"Host: 127.0.0.1\r\n"
                b"Content-Length: 0\r\n"
                b"\r\n"
            ),
        )

        assert left_response.startswith(b"HTTP/1.1 202 Accepted")
        assert right_response.startswith(b"HTTP/1.1 202 Accepted")
        left_batch = await left.fetch(1)
        right_batch = await right.fetch(1)
        assert len(left_batch) == 1
        assert len(right_batch) == 1
        assert left_batch[0].payload["path"] == "/left"
        assert right_batch[0].payload["method"] == "PUT"

        await left.close()
        await right.close()

    asyncio.run(scenario())


def test_webhook_source_rejects_invalid_json_and_wrong_method() -> None:
    async def scenario() -> None:
        port = _reserve_port()
        source = WebhookSource(path="/hooks/json", host="127.0.0.1", port=port, parser="json")
        await source.open()

        invalid_json = await _http_request(
            "127.0.0.1",
            port,
            (
                b"POST /hooks/json HTTP/1.1\r\n"
                b"Host: 127.0.0.1\r\n"
                b"Content-Type: application/json\r\n"
                b"Content-Length: 8\r\n"
                b"\r\n"
                b'{"bad":]'
            ),
        )
        wrong_method = await _http_request(
            "127.0.0.1",
            port,
            (
                b"GET /hooks/json HTTP/1.1\r\n"
                b"Host: 127.0.0.1\r\n"
                b"Content-Length: 0\r\n"
                b"\r\n"
            ),
        )

        assert invalid_json.startswith(b"HTTP/1.1 400 Bad Request")
        assert wrong_method.startswith(b"HTTP/1.1 405 Method Not Allowed")
        assert await source.fetch(1) == []

        await source.close()

    asyncio.run(scenario())


def test_webhook_source_accepts_valid_bearer_token() -> None:
    async def scenario() -> None:
        port = _reserve_port()
        source = WebhookSource(
            path="/hooks/protected",
            host="127.0.0.1",
            port=port,
            auth=BearerAuth("secret-token"),
        )
        await source.open()

        response = await _http_request(
            "127.0.0.1",
            port,
            (
                b"POST /hooks/protected HTTP/1.1\r\n"
                b"Host: 127.0.0.1\r\n"
                b"Authorization: Bearer secret-token\r\n"
                b"Content-Type: application/json\r\n"
                b"Content-Length: 12\r\n"
                b"\r\n"
                b'{"ok":"yes"}'
            ),
        )

        assert response.startswith(b"HTTP/1.1 202 Accepted")
        batch = await source.fetch(1)
        assert len(batch) == 1
        assert batch[0].payload["body"] == {"ok": "yes"}

        await source.close()

    asyncio.run(scenario())


def test_webhook_source_rejects_missing_or_invalid_bearer_token() -> None:
    async def scenario() -> None:
        port = _reserve_port()
        source = WebhookSource(
            path="/hooks/protected",
            host="127.0.0.1",
            port=port,
            auth=BearerAuth("secret-token"),
        )
        await source.open()

        missing = await _http_request(
            "127.0.0.1",
            port,
            (
                b"POST /hooks/protected HTTP/1.1\r\n"
                b"Host: 127.0.0.1\r\n"
                b"Content-Length: 0\r\n"
                b"\r\n"
            ),
        )
        invalid = await _http_request(
            "127.0.0.1",
            port,
            (
                b"POST /hooks/protected HTTP/1.1\r\n"
                b"Host: 127.0.0.1\r\n"
                b"Authorization: Bearer wrong-token\r\n"
                b"Content-Length: 0\r\n"
                b"\r\n"
            ),
        )

        assert missing.startswith(b"HTTP/1.1 401 Unauthorized")
        assert b'WWW-Authenticate: Bearer realm="webhook"' in missing
        assert invalid.startswith(b"HTTP/1.1 401 Unauthorized")
        assert await source.fetch(1) == []

        await source.close()

    asyncio.run(scenario())
