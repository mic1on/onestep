from __future__ import annotations

import httpx
import pytest
from onestep_elasticsearch.connector import ElasticsearchConnector

from onestep import (
    ConnectorErrorKind,
    ConnectorOperationError,
    Delivery,
    Envelope,
    OneStepApp,
    Source,
)


def test_mapping_becomes_one_newline_terminated_bulk_action() -> None:
    sink = ElasticsearchConnector("http://search:9200").bulk_sink(
        index="events", id_field="event_id"
    )
    chunks = sink._encode_chunks({"event_id": "evt-1", "value": 3})

    assert chunks == [
        b'{"index":{"_index":"events","_id":"evt-1"}}\n{"event_id":"evt-1","value":3}\n'
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
async def test_connector_round_robins_hosts_and_does_not_close_injected_client() -> (
    None
):
    seen: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"version": {"distribution": "opensearch"}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    connector = ElasticsearchConnector(
        ["http://one:9200", "http://two:9200"], client=client
    )

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
        "https://search:9200",
        username="writer",
        password="secret",
        headers={"X-Tenant": "blue"},
        client=client,
    )
    await connector.request_json("GET", "/")

    assert captured[0].headers["authorization"].startswith("Basic ")
    assert captured[0].headers["x-tenant"] == "blue"
    await client.aclose()


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        ({"api_key": "encoded-key"}, "ApiKey encoded-key"),
        ({"bearer_token": "token"}, "Bearer token"),
    ],
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
        "https://search:9200",
        ca_certs="/etc/ssl/search-ca.pem",
        client_cert="/etc/ssl/client.pem",
        client_key="/etc/ssl/client-key.pem",
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
        return httpx.Response(
            200, json={"errors": False, "items": [{"index": {"status": 201}}]}
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sink = ElasticsearchConnector("http://search:9200", client=client).bulk_sink(
        index="events", chunk_size=1
    )
    await sink.send(Envelope(body=[{"n": 1}, {"n": 2}]))
    assert len(calls) == 2
    await client.aclose()


@pytest.mark.asyncio
async def test_partial_auto_id_failure_is_uncertain() -> None:
    responses = [
        httpx.Response(
            200, json={"errors": False, "items": [{"index": {"status": 201}}]}
        ),
        httpx.Response(
            200,
            json={
                "errors": True,
                "items": [
                    {
                        "index": {
                            "status": 400,
                            "error": {
                                "type": "mapper_parsing_exception",
                                "reason": "bad field",
                            },
                        }
                    }
                ],
            },
        ),
    ]

    async def handler(request: httpx.Request) -> httpx.Response:
        return responses.pop(0)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sink = ElasticsearchConnector("http://search:9200", client=client).bulk_sink(
        index="events", chunk_size=1
    )
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
            return httpx.Response(
                200,
                json={
                    "errors": True,
                    "items": [
                        {"index": {"status": 201}},
                        {
                            "index": {
                                "status": 429,
                                "_id": "2",
                                "error": {"type": "rejected", "reason": "busy"},
                            }
                        },
                    ],
                },
            )
        return httpx.Response(
            200, json={"errors": False, "items": [{"index": {"status": 201}}]}
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sink = ElasticsearchConnector("http://search:9200", client=client).bulk_sink(
        index="events", id_field="id"
    )
    await sink.send(Envelope(body=[{"id": "1"}, {"id": "2"}]))
    assert bodies[1].count(b"\n") == 2
    assert b'"_id":"2"' in bodies[1]
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [502, 503, 504])
async def test_request_level_gateway_failure_without_stable_ids_is_not_replayed(
    status,
) -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status, json={"error": {"reason": "gateway failure"}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sink = ElasticsearchConnector("http://search:9200", client=client).bulk_sink(
        index="events", max_retries=2
    )

    with pytest.raises(ConnectorOperationError) as captured:
        await sink.send(Envelope(body={"value": 1}))

    assert calls == 1
    assert captured.value.kind is ConnectorErrorKind.UNCERTAIN
    await client.aclose()


@pytest.mark.asyncio
async def test_request_level_504_with_stable_ids_retries() -> None:
    responses = [
        httpx.Response(504, json={"error": {"reason": "gateway timeout"}}),
        httpx.Response(
            200,
            json={"errors": False, "items": [{"index": {"status": 201}}]},
        ),
    ]

    async def handler(request: httpx.Request) -> httpx.Response:
        return responses.pop(0)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sink = ElasticsearchConnector("http://search:9200", client=client).bulk_sink(
        index="events", id_field="id", max_retries=2
    )

    await sink.send(Envelope(body={"id": "evt-1", "value": 1}))

    assert responses == []
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "id_field", "expected_kind"),
    [
        (504, "id", ConnectorErrorKind.TRANSIENT),
        (429, None, ConnectorErrorKind.THROTTLED),
    ],
)
async def test_request_level_retry_exhaustion_is_bounded(
    status, id_field, expected_kind
) -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status, json={"error": {"reason": "unavailable"}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sink = ElasticsearchConnector("http://search:9200", client=client).bulk_sink(
        index="events", id_field=id_field, max_retries=2
    )

    with pytest.raises(ConnectorOperationError) as captured:
        await sink.send(Envelope(body={"id": "evt-1", "value": 1}))

    assert calls == 3
    assert captured.value.kind is expected_kind
    await client.aclose()


@pytest.mark.asyncio
async def test_create_with_id_field_does_not_claim_replay_safety() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(504, json={"error": {"reason": "gateway timeout"}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sink = ElasticsearchConnector("http://search:9200", client=client).bulk_sink(
        index="events", operation="create", id_field="id", max_retries=2
    )

    with pytest.raises(ConnectorOperationError) as captured:
        await sink.send(Envelope(body={"id": "evt-1", "value": 1}))

    assert calls == 1
    assert captured.value.kind is ConnectorErrorKind.UNCERTAIN
    await client.aclose()


@pytest.mark.asyncio
async def test_request_level_429_without_stable_ids_remains_retryable() -> None:
    responses = [
        httpx.Response(429, json={"error": {"reason": "rejected"}}),
        httpx.Response(
            200,
            json={"errors": False, "items": [{"index": {"status": 201}}]},
        ),
    ]

    async def handler(request: httpx.Request) -> httpx.Response:
        return responses.pop(0)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sink = ElasticsearchConnector("http://search:9200", client=client).bulk_sink(
        index="events", max_retries=2
    )

    await sink.send(Envelope(body={"value": 1}))

    assert responses == []
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("id_field", "expected_kind"),
    [
        (None, ConnectorErrorKind.UNCERTAIN),
        ("id", ConnectorErrorKind.PERMANENT),
    ],
)
async def test_missing_bulk_item_acknowledgement_is_classified_by_replay_safety(
    id_field, expected_kind
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"errors": False, "items": []})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sink = ElasticsearchConnector("http://search:9200", client=client).bulk_sink(
        index="events", id_field=id_field
    )
    with pytest.raises(ConnectorOperationError) as captured:
        await sink.send(Envelope(body={"id": "one"}))
    assert captured.value.kind is expected_kind
    assert captured.value.cause.items[0].status == 0
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"errors": False, "items": ["invalid"]},
        {"errors": False, "items": [{"index": {"status": "invalid"}}]},
        {"errors": True, "items": [{"index": {"status": 201}}]},
    ],
)
@pytest.mark.parametrize(
    ("id_field", "expected_kind"),
    [
        (None, ConnectorErrorKind.UNCERTAIN),
        ("id", ConnectorErrorKind.PERMANENT),
    ],
)
async def test_malformed_bulk_item_response_is_classified_by_replay_safety(
    payload, id_field, expected_kind
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sink = ElasticsearchConnector("http://search:9200", client=client).bulk_sink(
        index="events", id_field=id_field
    )

    with pytest.raises(ConnectorOperationError) as captured:
        await sink.send(Envelope(body={"id": "one"}))

    assert captured.value.kind is expected_kind
    assert captured.value.cause.items[0].error_type == "invalid_response"
    await client.aclose()


@pytest.mark.asyncio
async def test_send_streams_chunks_without_materializing_the_chunk_list(
    monkeypatch,
) -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200, json={"errors": False, "items": [{"index": {"status": 201}}]}
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sink = ElasticsearchConnector("http://search:9200", client=client).bulk_sink(
        index="events", chunk_size=1
    )

    def materialized_chunks_are_for_tests_only(body):
        raise AssertionError("send must stream encoded chunks")

    monkeypatch.setattr(sink, "_encode_chunks", materialized_chunks_are_for_tests_only)
    await sink.send(Envelope(body=[{"n": 1}, {"n": 2}]))

    assert calls == 2
    await client.aclose()


@pytest.mark.asyncio
async def test_oversized_action_is_permanent_without_a_network_call() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sink = ElasticsearchConnector("http://search:9200", client=client).bulk_sink(
        index="events", max_chunk_bytes=40
    )

    with pytest.raises(ConnectorOperationError) as captured:
        await sink.send(Envelope(body={"payload": "x" * 100}))

    assert captured.value.kind is ConnectorErrorKind.PERMANENT
    assert calls == 0
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("id_field", "expected_kind"),
    [
        (None, ConnectorErrorKind.UNCERTAIN),
        ("id", ConnectorErrorKind.PERMANENT),
    ],
)
async def test_non_object_bulk_response_is_classified_by_replay_safety(
    id_field, expected_kind
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sink = ElasticsearchConnector("http://search:9200", client=client).bulk_sink(
        index="events", id_field=id_field
    )

    with pytest.raises(ConnectorOperationError) as captured:
        await sink.send(Envelope(body={"id": "one"}))

    assert captured.value.kind is expected_kind
    await client.aclose()


@pytest.mark.asyncio
async def test_failed_retry_retains_original_logical_item_index() -> None:
    responses = [
        httpx.Response(
            200,
            json={
                "errors": True,
                "items": [
                    {"index": {"status": 201}},
                    {"index": {"status": 201}},
                    {
                        "index": {
                            "status": 429,
                            "_id": "three",
                            "error": {"type": "rejected", "reason": "busy"},
                        }
                    },
                ],
            },
        ),
        httpx.Response(
            200,
            json={
                "errors": True,
                "items": [
                    {
                        "index": {
                            "status": 400,
                            "_id": "three",
                            "error": {
                                "type": "mapper_parsing_exception",
                                "reason": "bad",
                            },
                        }
                    },
                ],
            },
        ),
    ]

    async def handler(request: httpx.Request) -> httpx.Response:
        return responses.pop(0)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sink = ElasticsearchConnector("http://search:9200", client=client).bulk_sink(
        index="events", id_field="id"
    )

    with pytest.raises(ConnectorOperationError) as captured:
        await sink.send(Envelope(body=[{"id": "one"}, {"id": "two"}, {"id": "three"}]))

    assert captured.value.cause.items[0].action_index == 2
    await client.aclose()


@pytest.mark.asyncio
async def test_bulk_diagnostics_redact_configured_credentials() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400, json={"error": {"reason": "token secret-key password secret-pass"}}
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    connector = ElasticsearchConnector(
        "http://search:9200",
        api_key="secret-key",
        headers={"X-Password": "secret-pass"},
        client=client,
    )
    sink = connector.bulk_sink(index="events")

    with pytest.raises(ConnectorOperationError) as captured:
        await sink.send(Envelope(body={"id": "one"}))

    diagnostic = str(captured.value.cause)
    assert "secret-key" not in diagnostic
    assert "secret-pass" not in diagnostic
    await client.aclose()


@pytest.mark.asyncio
async def test_transport_errors_redact_hosts_headers_and_generated_auth() -> None:
    host_password = "url@secret"
    header_secret = "custom-header-secret"
    basic_password = "basic-secret"

    class BrokenClient:
        async def request(self, *args, **kwargs):
            authorization = kwargs["headers"]["Authorization"]
            raise httpx.ConnectError(
                "cannot connect to "
                "https://url-user:url@secret@search.internal:9200 "
                f"with X-Api-Key={header_secret} and Authorization={authorization}"
            )

    connector = ElasticsearchConnector(
        "https://url-user:url%40secret@search.internal:9200",
        username="writer",
        password=basic_password,
        headers={"X-Api-Key": header_secret},
        client=BrokenClient(),
    )
    sink = connector.bulk_sink(index="events")

    with pytest.raises(ConnectorOperationError) as captured:
        await sink.send(Envelope(body={"id": "one"}))

    diagnostic = str(captured.value.cause)
    assert host_password not in diagnostic
    assert header_secret not in diagnostic
    assert basic_password not in diagnostic
    assert "Basic " not in diagnostic
    assert "search.internal" in diagnostic
    assert "<redacted>" in diagnostic


@pytest.mark.asyncio
async def test_missing_id_field_does_not_claim_replay_safety() -> None:
    responses = [
        httpx.Response(
            200, json={"errors": False, "items": [{"index": {"status": 201}}]}
        ),
        httpx.Response(
            200,
            json={
                "errors": True,
                "items": [
                    {
                        "index": {
                            "status": 400,
                            "error": {"type": "mapper", "reason": "bad"},
                        }
                    }
                ],
            },
        ),
    ]

    async def handler(request: httpx.Request) -> httpx.Response:
        return responses.pop(0)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sink = ElasticsearchConnector("http://search:9200", client=client).bulk_sink(
        index="events", id_field="id", chunk_size=1
    )
    with pytest.raises(ConnectorOperationError) as captured:
        await sink.send(Envelope(body=[{"id": "one"}, {"value": "missing-id"}]))

    assert captured.value.kind is ConnectorErrorKind.UNCERTAIN
    await client.aclose()


class _AckRecordingDelivery(Delivery):
    def __init__(self, envelope: Envelope) -> None:
        super().__init__(envelope)
        self.acked = False

    async def ack(self) -> None:
        self.acked = True

    async def retry(self, *, delay_s: float | None = None) -> None:
        raise AssertionError("runtime ordering test must not retry")

    async def fail(self, exc: Exception | None = None) -> None:
        raise AssertionError(f"runtime ordering test failed: {exc}")


class _OneShotSource(Source):
    poll_interval_s = 0.01

    def __init__(self, delivery: _AckRecordingDelivery) -> None:
        super().__init__("one-shot")
        self.delivery = delivery
        self.sent = False

    async def fetch(self, limit: int) -> list[Delivery]:
        if self.sent:
            return []
        self.sent = True
        return [self.delivery]


@pytest.mark.asyncio
async def test_runtime_ack_follows_backend_bulk_acknowledgement() -> None:
    import asyncio

    release = asyncio.Event()
    entered = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        entered.set()
        await release.wait()
        return httpx.Response(
            200, json={"errors": False, "items": [{"index": {"status": 201}}]}
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sink = ElasticsearchConnector("http://search:9200", client=client).bulk_sink(
        index="events", id_field="id"
    )
    delivery = _AckRecordingDelivery(Envelope(body={"id": "1"}))
    source = _OneShotSource(delivery)
    app = OneStepApp("elasticsearch-runtime-order", shutdown_timeout_s=1.0)

    @app.task(source=source, emit=sink, concurrency=1)
    async def forward(ctx, item):
        ctx.app.request_shutdown()
        return item

    serving = asyncio.create_task(app.serve())
    await entered.wait()
    assert delivery.acked is False
    release.set()
    await asyncio.wait_for(serving, timeout=2.0)
    assert delivery.acked is True
    await client.aclose()
