from __future__ import annotations

import asyncio
import json
from importlib import metadata as importlib_metadata
from typing import Any

import pytest

from onestep import Envelope, InMemoryCursorStore
from onestep.config import load_app_config
from onestep.resilience import ConnectorErrorKind, ConnectorOperation, ConnectorOperationError
from onestep_feishu_bitable import (
    FeishuBitableApiError,
    FeishuBitableConnector,
    FeishuBitableIncrementalSource,
    FeishuBitableTableSink,
    feishu_bitable_text,
    feishu_bitable_user,
)


def test_package_exposes_onestep_resource_entry_point() -> None:
    entry_points = _entry_points_for_group("onestep.resources")

    assert any(
        entry_point.name == "feishu_bitable"
        and entry_point.value == "onestep_feishu_bitable:register"
        for entry_point in entry_points
    )


def _entry_points_for_group(group: str) -> tuple[Any, ...]:
    entry_points = importlib_metadata.entry_points()
    if hasattr(entry_points, "select"):
        return tuple(entry_points.select(group=group))
    return tuple(entry_points.get(group, ()))


async def _start_json_server(
    handler,
) -> tuple[asyncio.AbstractServer, list[dict[str, Any]], str]:
    requests: list[dict[str, Any]] = []

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        request = await _read_request(reader)
        requests.append(request)
        status, payload = handler(request)
        body = json.dumps(payload).encode("utf-8")
        response = (
            f"HTTP/1.1 {status} {_reason(status)}\r\n"
            "Content-Type: application/json\r\n"
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
    raw_body = await reader.readexactly(content_length) if content_length else b""
    return {
        "method": method,
        "target": target,
        "headers": headers,
        "body": json.loads(raw_body.decode("utf-8")) if raw_body else None,
    }


def _reason(status: int) -> str:
    return {
        200: "OK",
        403: "Forbidden",
        429: "Too Many Requests",
    }.get(status, "Status")


async def _close_server(server: asyncio.AbstractServer) -> None:
    server.close()
    await server.wait_closed()


def test_feishu_connector_fetches_and_reuses_tenant_token_for_create_sink() -> None:
    async def scenario() -> None:
        def handler(request: dict[str, Any]) -> tuple[int, dict[str, Any]]:
            if request["target"] == "/open-apis/auth/v3/tenant_access_token/internal":
                return 200, {"code": 0, "tenant_access_token": "tenant-token", "expire": 7200}
            if request["target"] == "/open-apis/bitable/v1/apps/app-token/tables/tbl/records":
                return 200, {"code": 0, "data": {"record": {"record_id": "rec1"}}}
            return 403, {"code": 999, "msg": "forbidden"}

        server, requests, base_url = await _start_json_server(handler)
        try:
            connector = FeishuBitableConnector(app_id="app-id", app_secret="secret", base_url=base_url)
            sink = connector.table_sink(
                app_token="app-token",
                table_id="tbl",
                mode="create",
            )
            await sink.send(Envelope(body={"order_no": "A001"}))
            await sink.send(Envelope(body={"order_no": "A002"}))
        finally:
            await _close_server(server)

        token_requests = [request for request in requests if request["target"].endswith("tenant_access_token/internal")]
        create_requests = [request for request in requests if request["target"].endswith("/records")]
        assert len(token_requests) == 1
        assert len(create_requests) == 2
        assert create_requests[0]["headers"]["authorization"] == "Bearer tenant-token"
        assert create_requests[0]["body"] == {"fields": {"order_no": "A001"}}

    asyncio.run(scenario())


def test_feishu_bitable_text_flattens_common_field_values() -> None:
    import onestep

    assert not hasattr(onestep, "FeishuBitableConnector")
    assert not hasattr(onestep, "feishu_bitable_text")
    assert feishu_bitable_text(None) is None
    assert feishu_bitable_text("plain") == "plain"
    assert feishu_bitable_text([{"text": "A"}, {"text": "B"}]) == "AB"
    assert feishu_bitable_text({"name": "Alice"}) == "Alice"
    assert feishu_bitable_text({"unknown": 1}) == '{"unknown": 1}'


def test_feishu_bitable_user_formats_person_field_values() -> None:
    import onestep

    assert not hasattr(onestep, "feishu_bitable_user")
    assert feishu_bitable_user(None) is None
    assert feishu_bitable_user("") is None
    assert feishu_bitable_user("u_123") == [{"id": "u_123"}]
    assert feishu_bitable_user({"id": "u_123", "name": "Alice"}) == [{"id": "u_123"}]
    assert feishu_bitable_user({"user_id": "u_123"}) == [{"id": "u_123"}]
    assert feishu_bitable_user([{"open_id": "ou_1"}, "u_2", None]) == [{"id": "ou_1"}, {"id": "u_2"}]


def test_feishu_incremental_source_advances_cursor_in_ack_order_and_resumes() -> None:
    async def scenario() -> None:
        connector = FeishuBitableConnector(app_id="app-id", app_secret="secret")
        search_bodies: list[dict[str, Any]] = []

        async def search_records(**kwargs):
            search_bodies.append(kwargs["body"])
            return {
                "items": [
                    {"record_id": "rec1", "fields": {"updated_at": 10, "order_no": "A001"}},
                    {"record_id": "rec2", "fields": {"updated_at": 10, "order_no": "A002"}},
                ],
                "has_more": False,
            }

        connector.search_records = search_records  # type: ignore[method-assign]
        state = InMemoryCursorStore()
        source = connector.incremental(
            app_token="app-token",
            table_id="tbl",
            cursor_field="updated_at",
            state=state,
            state_key="sync",
        )

        batch = await source.fetch(10)
        assert [delivery.payload["record_id"] for delivery in batch] == ["rec1", "rec2"]
        assert batch[0].payload["fields"]["order_no"] == "A001"

        await batch[1].ack()
        assert await state.load("sync") is None

        next_batch = await source.fetch(10)
        assert next_batch == []

        await batch[0].ack()
        assert await state.load("sync") == [10, "rec2"]

        async def search_after_restart(**kwargs):
            search_bodies.append(kwargs["body"])
            return {
                "items": [
                    {"record_id": "rec1", "fields": {"updated_at": 10, "order_no": "A001"}},
                    {"record_id": "rec2", "fields": {"updated_at": 10, "order_no": "A002"}},
                    {"record_id": "rec3", "fields": {"updated_at": 10, "order_no": "A003"}},
                    {"record_id": "rec4", "fields": {"updated_at": 11, "order_no": "A004"}},
                ],
                "has_more": False,
            }

        connector.search_records = search_after_restart  # type: ignore[method-assign]
        restarted = connector.incremental(
            app_token="app-token",
            table_id="tbl",
            cursor_field="updated_at",
            state=state,
            state_key="sync",
        )
        resumed_batch = await restarted.fetch(10)
        assert [delivery.payload["record_id"] for delivery in resumed_batch] == ["rec3", "rec4"]
        assert search_bodies[-1] == {
            "automatic_fields": True,
            "sort": [{"field_name": "updated_at", "desc": False}],
        }

    asyncio.run(scenario())


def test_feishu_incremental_source_can_cursor_on_automatic_modified_time_alias() -> None:
    async def scenario() -> None:
        connector = FeishuBitableConnector(app_id="app-id", app_secret="secret")

        async def search_records(**kwargs):
            return {
                "items": [
                    {
                        "record_id": "rec1",
                        "last_modified_time": 100,
                        "fields": {"order_no": "A001"},
                    }
                ],
                "has_more": False,
            }

        connector.search_records = search_records  # type: ignore[method-assign]
        source = connector.incremental(
            app_token="app-token",
            table_id="tbl",
            cursor_field="最后更新时间",
        )

        batch = await source.fetch(10)
        assert len(batch) == 1
        assert batch[0].payload["last_modified_time"] == 100
        await batch[0].ack()
        assert await source.state.load(source.state_key) == [100, "rec1"]

    asyncio.run(scenario())


def test_feishu_incremental_source_falls_back_when_sort_is_invalid() -> None:
    async def scenario() -> None:
        connector = FeishuBitableConnector(app_id="app-id", app_secret="secret")
        search_bodies: list[dict[str, Any]] = []

        async def search_records(**kwargs):
            search_bodies.append(kwargs["body"])
            if "sort" in kwargs["body"]:
                api_error = FeishuBitableApiError(
                    status=400,
                    reason="Bad Request",
                    code=1254000,
                    message="InvalidSort",
                    body={"code": 1254000, "msg": "InvalidSort"},
                )
                raise ConnectorOperationError(
                    backend="feishu_bitable",
                    operation=ConnectorOperation.FETCH,
                    kind=ConnectorErrorKind.PERMANENT,
                    source_name=kwargs["source_name"],
                    cause=api_error,
                    message="InvalidSort",
                ) from api_error
            if kwargs.get("page_token") is None:
                return {
                    "items": [
                        {
                            "record_id": "rec2",
                            "last_modified_time": 200,
                            "fields": {"编号": "A002"},
                        }
                    ],
                    "has_more": True,
                    "page_token": "next-page",
                }
            return {
                "items": [
                    {
                        "record_id": "rec1",
                        "last_modified_time": 100,
                        "fields": {"编号": "A001"},
                    }
                ],
                "has_more": False,
            }

        connector.search_records = search_records  # type: ignore[method-assign]
        source = connector.incremental(
            app_token="app-token",
            table_id="tbl",
            cursor_field="最后更新时间",
        )

        batch = await source.fetch(10)
        assert [delivery.payload["record_id"] for delivery in batch] == ["rec1", "rec2"]
        assert search_bodies == [
            {
                "automatic_fields": True,
                "sort": [{"field_name": "last_modified_time", "desc": False}],
            },
            {"automatic_fields": True},
            {"automatic_fields": True},
        ]

    asyncio.run(scenario())


def test_feishu_incremental_source_fetch_limit_caps_batch_size() -> None:
    async def scenario() -> None:
        connector = FeishuBitableConnector(app_id="app-id", app_secret="secret")
        page_sizes: list[int] = []

        async def search_records(**kwargs):
            page_sizes.append(kwargs["page_size"])
            return {
                "items": [
                    {"record_id": f"rec{index}", "fields": {"updated_at": index}}
                    for index in range(1, 21)
                ],
                "has_more": False,
            }

        connector.search_records = search_records  # type: ignore[method-assign]
        source = connector.incremental(
            app_token="app-token",
            table_id="tbl",
            cursor_field="updated_at",
            batch_size=20,
        )

        single = await source.fetch(1)
        assert len(single) == 1
        assert page_sizes[-1] == 1

        await single[0].ack()
        batch = await source.fetch(20)
        assert len(batch) == 19
        assert page_sizes[-1] == 20

    asyncio.run(scenario())


def test_feishu_table_sink_upsert_creates_updates_and_rejects_duplicates() -> None:
    async def scenario() -> None:
        connector = FeishuBitableConnector(app_id="app-id", app_secret="secret")
        search_results = [
            [],
            [{"record_id": "rec1", "fields": {"order_no": "A001"}}],
            [
                {"record_id": "rec1", "fields": {"order_no": "A001"}},
                {"record_id": "rec2", "fields": {"order_no": "A001"}},
            ],
        ]
        created: list[dict[str, Any]] = []
        updated: list[dict[str, Any]] = []
        search_bodies: list[dict[str, Any]] = []

        async def search_records(**kwargs):
            search_bodies.append(kwargs["body"])
            return {"items": search_results.pop(0), "has_more": False}

        async def create_record(**kwargs):
            created.append(kwargs)
            return {"record": {"record_id": "created"}}

        async def update_record(**kwargs):
            updated.append(kwargs)
            return {"record": {"record_id": kwargs["record_id"]}}

        connector.search_records = search_records  # type: ignore[method-assign]
        connector.create_record = create_record  # type: ignore[method-assign]
        connector.update_record = update_record  # type: ignore[method-assign]
        sink = connector.table_sink(
            app_token="app-token",
            table_id="tbl",
            mode="upsert",
            match_fields=["order_no"],
        )

        await sink.send(Envelope(body={"order_no": "A001", "status": "new"}))
        await sink.send(Envelope(body={"fields": {"order_no": "A001", "status": "paid"}}))

        with pytest.raises(ConnectorOperationError) as raised:
            await sink.send(Envelope(body={"order_no": "A001"}))

        assert created[0]["fields"] == {"order_no": "A001", "status": "new"}
        assert updated[0]["record_id"] == "rec1"
        assert updated[0]["fields"] == {"order_no": "A001", "status": "paid"}
        assert search_bodies[0]["filter"]["conditions"][0] == {
            "field_name": "order_no",
            "operator": "is",
            "value": ["A001"],
        }
        assert raised.value.kind is ConnectorErrorKind.PERMANENT

    asyncio.run(scenario())


def test_feishu_table_sink_upsert_matches_multiple_fields() -> None:
    async def scenario() -> None:
        connector = FeishuBitableConnector(app_id="app-id", app_secret="secret")
        search_bodies: list[dict[str, Any]] = []
        updated: list[dict[str, Any]] = []

        async def search_records(**kwargs):
            search_bodies.append(kwargs["body"])
            return {"items": [{"record_id": "rec1", "fields": {"shop_id": "S01", "order_no": "A001"}}]}

        async def update_record(**kwargs):
            updated.append(kwargs)
            return {"record": {"record_id": kwargs["record_id"]}}

        connector.search_records = search_records  # type: ignore[method-assign]
        connector.update_record = update_record  # type: ignore[method-assign]
        sink = connector.table_sink(
            app_token="app-token",
            table_id="tbl",
            mode="upsert",
            match_fields=["shop_id", "order_no"],
        )

        await sink.send(Envelope(body={"shop_id": "S01", "order_no": "A001", "status": "paid"}))

        assert search_bodies[0]["filter"] == {
            "conjunction": "and",
            "conditions": [
                {
                    "field_name": "shop_id",
                    "operator": "is",
                    "value": ["S01"],
                },
                {
                    "field_name": "order_no",
                    "operator": "is",
                    "value": ["A001"],
                },
            ],
        }
        assert updated[0]["record_id"] == "rec1"

    asyncio.run(scenario())


def test_feishu_requests_include_user_id_type_query_parameter() -> None:
    async def scenario() -> None:
        def handler(request: dict[str, Any]) -> tuple[int, dict[str, Any]]:
            if request["target"] == "/open-apis/auth/v3/tenant_access_token/internal":
                return 200, {"code": 0, "tenant_access_token": "tenant-token", "expire": 7200}
            if request["target"].startswith(
                "/open-apis/bitable/v1/apps/app-token/tables/tbl/records/search"
            ):
                return 200, {"code": 0, "data": {"items": [], "has_more": False}}
            if request["target"].startswith("/open-apis/bitable/v1/apps/app-token/tables/tbl/records"):
                return 200, {"code": 0, "data": {"record": {"record_id": "rec1"}}}
            return 403, {"code": 999, "msg": "forbidden"}

        server, requests, base_url = await _start_json_server(handler)
        try:
            connector = FeishuBitableConnector(app_id="app-id", app_secret="secret", base_url=base_url)
            source = connector.incremental(
                app_token="app-token",
                table_id="tbl",
                cursor_field="updated_at",
                user_id_type="user_id",
            )
            sink = connector.table_sink(
                app_token="app-token",
                table_id="tbl",
                mode="create",
                user_id_type="user_id",
            )

            await source.fetch(20)
            await sink.send(Envelope(body={"owner": feishu_bitable_user("u_123")}))
        finally:
            await _close_server(server)

        search_request = next(request for request in requests if "/records/search?" in request["target"])
        create_request = next(
            request for request in requests if request["target"].endswith("/records?user_id_type=user_id")
        )
        assert search_request["target"].endswith("/records/search?page_size=20&user_id_type=user_id")
        assert create_request["target"].endswith("/records?user_id_type=user_id")
        assert create_request["body"] == {"fields": {"owner": [{"id": "u_123"}]}}

    asyncio.run(scenario())


def test_feishu_incremental_source_rejects_records_missing_cursor_field() -> None:
    async def scenario() -> None:
        connector = FeishuBitableConnector(app_id="app-id", app_secret="secret")

        async def search_records(**kwargs):
            return {
                "items": [
                    {"record_id": "rec1", "fields": {"order_no": "A001"}},
                ],
                "has_more": False,
            }

        connector.search_records = search_records  # type: ignore[method-assign]
        source = connector.incremental(
            app_token="app-token",
            table_id="tbl",
            cursor_field="updated_at",
        )

        with pytest.raises(ConnectorOperationError) as raised:
            await source.fetch(10)

        assert raised.value.kind is ConnectorErrorKind.PERMANENT

    asyncio.run(scenario())


def test_feishu_table_sink_update_requires_existing_match() -> None:
    async def scenario() -> None:
        connector = FeishuBitableConnector(app_id="app-id", app_secret="secret")

        async def search_records(**kwargs):
            return {"items": [], "has_more": False}

        connector.search_records = search_records  # type: ignore[method-assign]
        sink = connector.table_sink(
            app_token="app-token",
            table_id="tbl",
            mode="update",
            match_fields=["order_no"],
        )

        with pytest.raises(ConnectorOperationError) as raised:
            await sink.send(Envelope(body={"order_no": "A001"}))

        assert raised.value.kind is ConnectorErrorKind.PERMANENT

    asyncio.run(scenario())


def test_feishu_table_sink_rejects_missing_match_fields_payload() -> None:
    async def scenario() -> None:
        connector = FeishuBitableConnector(app_id="app-id", app_secret="secret")
        sink = connector.table_sink(
            app_token="app-token",
            table_id="tbl",
            mode="upsert",
            match_fields=["shop_id", "order_no"],
        )

        with pytest.raises(ConnectorOperationError) as raised:
            await sink.send(Envelope(body={"shop_id": "S01", "status": "new"}))

        assert raised.value.kind is ConnectorErrorKind.PERMANENT

    asyncio.run(scenario())


def test_yaml_builds_feishu_bitable_resources_in_strict_mode() -> None:
    app = load_app_config(
        {
            "apiVersion": "onestep/v1alpha1",
            "kind": "App",
            "app": {"name": "feishu-sync"},
            "resources": {
                "feishu_bitable": {
                    "type": "feishu_bitable",
                    "app_id": "app-id",
                    "app_secret": "secret",
                },
                "source": {
                    "type": "feishu_bitable_incremental",
                    "connector": "feishu_bitable",
                    "app_token": "app-token",
                    "table_id": "src",
                    "cursor_field": "updated_at",
                    "user_id_type": "user_id",
                },
                "sink": {
                    "type": "feishu_bitable_table_sink",
                    "connector": "feishu_bitable",
                    "app_token": "app-token",
                    "table_id": "dst",
                    "match_fields": ["shop_id", "order_no"],
                    "user_id_type": "user_id",
                },
            },
            "tasks": [
                {
                    "name": "copy",
                    "source": "source",
                    "emit": "sink",
                }
            ],
        },
        strict=True,
    )

    assert isinstance(app.resources["feishu_bitable"], FeishuBitableConnector)
    assert isinstance(app.resources["source"], FeishuBitableIncrementalSource)
    assert isinstance(app.resources["sink"], FeishuBitableTableSink)
    assert app.resources["source"].user_id_type == "user_id"
    assert app.resources["sink"].user_id_type == "user_id"
    assert app.resources["sink"].match_fields == ("shop_id", "order_no")


def test_yaml_rejects_unknown_feishu_fields_in_strict_mode() -> None:
    with pytest.raises(ValueError, match="unsupported fields for resources.feishu_bitable: token"):
        load_app_config(
            {
                "apiVersion": "onestep/v1alpha1",
                "kind": "App",
                "app": {"name": "feishu-sync"},
                "resources": {
                    "feishu_bitable": {
                        "type": "feishu_bitable",
                        "app_id": "app-id",
                        "app_secret": "secret",
                        "token": "unexpected",
                    },
                },
                "tasks": [],
            },
            strict=True,
        )


def test_yaml_rejects_upsert_sink_without_match_fields_in_strict_mode() -> None:
    with pytest.raises(ValueError, match="'resources.sink.match_fields' must be a non-empty list of strings"):
        load_app_config(
            {
                "apiVersion": "onestep/v1alpha1",
                "kind": "App",
                "app": {"name": "feishu-sync"},
                "resources": {
                    "feishu_bitable": {
                        "type": "feishu_bitable",
                        "app_id": "app-id",
                        "app_secret": "secret",
                    },
                    "sink": {
                        "type": "feishu_bitable_table_sink",
                        "connector": "feishu_bitable",
                        "app_token": "app-token",
                        "table_id": "dst",
                    },
                },
                "tasks": [],
            },
            strict=True,
        )


def test_yaml_rejects_legacy_match_field_in_strict_mode() -> None:
    with pytest.raises(ValueError, match="unsupported fields for resources.sink: match_field"):
        load_app_config(
            {
                "apiVersion": "onestep/v1alpha1",
                "kind": "App",
                "app": {"name": "feishu-sync"},
                "resources": {
                    "feishu_bitable": {
                        "type": "feishu_bitable",
                        "app_id": "app-id",
                        "app_secret": "secret",
                    },
                    "sink": {
                        "type": "feishu_bitable_table_sink",
                        "connector": "feishu_bitable",
                        "app_token": "app-token",
                        "table_id": "dst",
                        "match_field": "order_no",
                    },
                },
                "tasks": [],
            },
            strict=True,
        )


def test_yaml_rejects_invalid_match_fields_in_strict_mode() -> None:
    with pytest.raises(TypeError, match="'resources.sink.match_fields' must be a non-empty list of strings"):
        load_app_config(
            {
                "apiVersion": "onestep/v1alpha1",
                "kind": "App",
                "app": {"name": "feishu-sync"},
                "resources": {
                    "feishu_bitable": {
                        "type": "feishu_bitable",
                        "app_id": "app-id",
                        "app_secret": "secret",
                    },
                    "sink": {
                        "type": "feishu_bitable_table_sink",
                        "connector": "feishu_bitable",
                        "app_token": "app-token",
                        "table_id": "dst",
                        "match_fields": "order_no",
                    },
                },
                "tasks": [],
            },
            strict=True,
        )


def test_yaml_rejects_invalid_feishu_user_id_type_in_strict_mode() -> None:
    with pytest.raises(ValueError, match="unsupported resources.sink.user_id_type"):
        load_app_config(
            {
                "apiVersion": "onestep/v1alpha1",
                "kind": "App",
                "app": {"name": "feishu-sync"},
                "resources": {
                    "feishu_bitable": {
                        "type": "feishu_bitable",
                        "app_id": "app-id",
                        "app_secret": "secret",
                    },
                    "sink": {
                        "type": "feishu_bitable_table_sink",
                        "connector": "feishu_bitable",
                        "app_token": "app-token",
                        "table_id": "dst",
                        "mode": "create",
                        "user_id_type": "email",
                    },
                },
                "tasks": [],
            },
            strict=True,
        )


def test_feishu_descriptors_redact_app_token_and_omit_credentials() -> None:
    connector = FeishuBitableConnector(app_id="app-id", app_secret="secret")
    source = connector.incremental(
        app_token="app-token-secret",
        table_id="tbl",
        cursor_field="updated_at",
        state_key="state-key",
    )
    sink = connector.table_sink(
        app_token="app-token-secret",
        table_id="tbl",
        mode="upsert",
        match_fields=["order_no"],
    )

    payload = json.dumps(
        {
            "source": source.control_plane_descriptor(),
            "sink": sink.control_plane_descriptor(),
        }
    )

    assert "secret" not in payload
    assert '"app_token": "<redacted>"' in payload
    assert "order_no" in payload
    assert "updated_at" in payload


def test_feishu_http_error_classifies_rate_limit_as_throttled() -> None:
    async def scenario() -> None:
        def handler(request: dict[str, Any]) -> tuple[int, dict[str, Any]]:
            if request["target"] == "/open-apis/auth/v3/tenant_access_token/internal":
                return 200, {"code": 0, "tenant_access_token": "tenant-token", "expire": 7200}
            return 429, {"code": 99991663, "msg": "rate limit"}

        server, requests, base_url = await _start_json_server(handler)
        try:
            connector = FeishuBitableConnector(app_id="app-id", app_secret="secret", base_url=base_url)
            sink = connector.table_sink(
                app_token="app-token",
                table_id="tbl",
                mode="create",
            )
            with pytest.raises(ConnectorOperationError) as raised:
                await sink.send(Envelope(body={"order_no": "A001"}))
        finally:
            await _close_server(server)

        assert len(requests) == 2
        assert raised.value.kind is ConnectorErrorKind.THROTTLED

    asyncio.run(scenario())
