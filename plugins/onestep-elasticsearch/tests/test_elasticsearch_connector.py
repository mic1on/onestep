from __future__ import annotations

import pytest
import httpx

from onestep import ConnectorErrorKind, ConnectorOperationError, Envelope
from onestep_elasticsearch.connector import ElasticsearchConnector


def test_mapping_becomes_one_newline_terminated_bulk_action() -> None:
    sink = ElasticsearchConnector("http://search:9200").bulk_sink(
        index="events", id_field="event_id"
    )
    chunks = sink._encode_chunks({"event_id": "evt-1", "value": 3})

    assert chunks == [
        b'{"index":{"_index":"events","_id":"evt-1"}}\n'
        b'{"event_id":"evt-1","value":3}\n'
    ]


def test_sequence_chunks_by_action_count() -> None:
    sink = ElasticsearchConnector("http://search:9200").bulk_sink(
        index="events", chunk_size=2
    )
    chunks = sink._encode_chunks([{"n": 1}, {"n": 2}, {"n": 3}])

    assert len(chunks) == 2
    assert chunks[0].count(b"\n") == 4
    assert chunks[1].count(b"\n") == 2


@pytest.mark.parametrize("body", [[], "text", ["text"], [{"ok": 1}, 2]])
def test_invalid_logical_batch_is_rejected(body) -> None:
    sink = ElasticsearchConnector("http://search:9200").bulk_sink(index="events")

    with pytest.raises((TypeError, ValueError)):
        sink._encode_chunks(body)


def test_one_action_larger_than_byte_limit_is_rejected() -> None:
    sink = ElasticsearchConnector("http://search:9200").bulk_sink(
        index="events", max_chunk_bytes=40
    )

    with pytest.raises(ValueError, match="max_chunk_bytes"):
        sink._encode_chunks({"payload": "x" * 100})


@pytest.mark.asyncio
async def test_connector_round_robins_hosts_and_does_not_close_injected_client() -> None:
    seen: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"version": {"distribution": "opensearch"}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    connector = ElasticsearchConnector(["http://one:9200", "http://two:9200"], client=client)

    await connector.request_json("GET", "/")
    await connector.request_json("GET", "/")
    await connector.close()

    assert seen == ["http://one:9200/", "http://two:9200/"]
    assert client.is_closed is False
    await client.aclose()


@pytest.mark.asyncio
async def test_basic_auth_and_custom_headers_are_applied() -> None:
    captured: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    connector = ElasticsearchConnector(
        "https://search:9200", username="writer", password="secret",
        headers={"X-Tenant": "blue"}, client=client,
    )
    await connector.request_json("GET", "/")

    assert captured[0].headers["authorization"].startswith("Basic ")
    assert captured[0].headers["x-tenant"] == "blue"
    await client.aclose()


@pytest.mark.parametrize(
    ("options", "expected"),
    [({"api_key": "encoded-key"}, "ApiKey encoded-key"), ({"bearer_token": "token"}, "Bearer token")],
)
@pytest.mark.asyncio
async def test_api_key_and_bearer_auth_headers(options, expected) -> None:
    captured: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    connector = ElasticsearchConnector("https://search:9200", client=client, **options)
    await connector.request_json("GET", "/")
    assert captured[0].headers["authorization"] == expected
    await client.aclose()


@pytest.mark.asyncio
async def test_owned_client_receives_ca_and_client_certificate(monkeypatch) -> None:
    captured: dict[str, object] = {}
    close_calls = 0

    class BuiltClient:
        async def aclose(self) -> None:
            nonlocal close_calls
            close_calls += 1

    def build_client(**options):
        captured.update(options)
        return BuiltClient()

    monkeypatch.setattr(httpx, "AsyncClient", build_client)
    connector = ElasticsearchConnector(
        "https://search:9200", ca_certs="/etc/ssl/search-ca.pem",
        client_cert="/etc/ssl/client.pem", client_key="/etc/ssl/client-key.pem",
    )
    await connector._get_client()
    assert captured == {
        "verify": "/etc/ssl/search-ca.pem",
        "cert": ("/etc/ssl/client.pem", "/etc/ssl/client-key.pem"),
    }
    await connector.close()
    await connector.close()
    assert close_calls == 1


@pytest.mark.asyncio
async def test_send_waits_for_every_success_item() -> None:
    calls: list[bytes] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.content)
        return httpx.Response(200, json={"errors": False, "items": [{"index": {"status": 201}}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sink = ElasticsearchConnector("http://search:9200", client=client).bulk_sink(index="events", chunk_size=1)
    await sink.send(Envelope(body=[{"n": 1}, {"n": 2}]))
    assert len(calls) == 2
    await client.aclose()


@pytest.mark.asyncio
async def test_partial_auto_id_failure_is_uncertain() -> None:
    responses = [
        httpx.Response(200, json={"errors": False, "items": [{"index": {"status": 201}}]}),
        httpx.Response(200, json={"errors": True, "items": [{"index": {"status": 400, "error": {"type": "mapper_parsing_exception", "reason": "bad field"}}}]}),
    ]

    async def handler(request: httpx.Request) -> httpx.Response:
        return responses.pop(0)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sink = ElasticsearchConnector("http://search:9200", client=client).bulk_sink(index="events", chunk_size=1)
    with pytest.raises(ConnectorOperationError) as captured:
        await sink.send(Envelope(body=[{"n": 1}, {"n": "bad"}]))
    assert captured.value.kind is ConnectorErrorKind.UNCERTAIN
    assert captured.value.cause.items[0].status == 400
    await client.aclose()


@pytest.mark.asyncio
async def test_429_retries_only_failed_subset() -> None:
    bodies: list[bytes] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(request.content)
        if len(bodies) == 1:
            return httpx.Response(200, json={"errors": True, "items": [
                {"index": {"status": 201}},
                {"index": {"status": 429, "_id": "2", "error": {"type": "rejected", "reason": "busy"}}},
            ]})
        return httpx.Response(200, json={"errors": False, "items": [{"index": {"status": 201}}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sink = ElasticsearchConnector("http://search:9200", client=client).bulk_sink(index="events", id_field="id")
    await sink.send(Envelope(body=[{"id": "1"}, {"id": "2"}]))
    assert bodies[1].count(b"\n") == 2
    assert b'"_id":"2"' in bodies[1]
    await client.aclose()


@pytest.mark.asyncio
async def test_missing_bulk_item_acknowledgement_is_permanent() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"errors": False, "items": []})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sink = ElasticsearchConnector("http://search:9200", client=client).bulk_sink(index="events")
    with pytest.raises(ConnectorOperationError) as captured:
        await sink.send(Envelope(body={"id": "one"}))
    assert captured.value.kind is ConnectorErrorKind.PERMANENT
    assert captured.value.cause.items[0].status == 0
    await client.aclose()
