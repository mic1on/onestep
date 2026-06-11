from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from onestep.resource_registry import ResourceBuildContext, ResourceRegistry, ResourceSpecHandler

from .connector import MySQLConnector

_MYSQL_FIELDS = frozenset({"type", "dsn", "engine_options"})
_MYSQL_STATE_STORE_FIELDS = frozenset(
    {"type", "connector", "table", "key_column", "value_column", "updated_at_column", "auto_create"}
)
_MYSQL_CURSOR_STORE_FIELDS = frozenset(
    {"type", "connector", "table", "key_column", "value_column", "updated_at_column", "auto_create"}
)
_MYSQL_TABLE_QUEUE_FIELDS = frozenset(
    {"type", "connector", "table", "key", "where", "claim", "ack", "nack", "batch_size", "poll_interval_s"}
)
_MYSQL_INCREMENTAL_FIELDS = frozenset(
    {"type", "connector", "table", "key", "cursor", "where", "batch_size", "poll_interval_s", "state", "state_key"}
)
_MYSQL_TABLE_SINK_FIELDS = frozenset({"type", "connector", "table", "mode", "keys"})


def register_resources(registry: ResourceRegistry) -> None:
    registry.register_resource_type(
        ResourceSpecHandler(
            type="mysql",
            allowed_fields=_MYSQL_FIELDS,
            build=_build_mysql,
        )
    )
    registry.register_resource_type(
        ResourceSpecHandler(
            type="mysql_state_store",
            allowed_fields=_MYSQL_STATE_STORE_FIELDS,
            build=_build_mysql_state_store,
        )
    )
    registry.register_resource_type(
        ResourceSpecHandler(
            type="mysql_cursor_store",
            allowed_fields=_MYSQL_CURSOR_STORE_FIELDS,
            build=_build_mysql_cursor_store,
        )
    )
    registry.register_resource_type(
        ResourceSpecHandler(
            type="mysql_table_queue",
            allowed_fields=_MYSQL_TABLE_QUEUE_FIELDS,
            build=_build_mysql_table_queue,
        )
    )
    registry.register_resource_type(
        ResourceSpecHandler(
            type="mysql_incremental",
            allowed_fields=_MYSQL_INCREMENTAL_FIELDS,
            build=_build_mysql_incremental,
        )
    )
    registry.register_resource_type(
        ResourceSpecHandler(
            type="mysql_table_sink",
            allowed_fields=_MYSQL_TABLE_SINK_FIELDS,
            build=_build_mysql_table_sink,
        )
    )


def _build_mysql(ctx: ResourceBuildContext, spec: Mapping[str, Any]) -> MySQLConnector:
    return MySQLConnector(
        ctx.require_string(spec, "dsn"),
        **ctx.mapping_value(spec.get("engine_options"), field=f"{ctx.field}.engine_options"),
    )


def _build_mysql_state_store(ctx: ResourceBuildContext, spec: Mapping[str, Any]) -> Any:
    connector = ctx.resolve_dependency(spec, "connector")
    if not hasattr(connector, "state_store"):
        raise TypeError(f"resource {spec['connector']!r} cannot build mysql_state_store")
    return connector.state_store(
        table=spec.get("table", "onestep_state"),
        key_column=spec.get("key_column", "state_key"),
        value_column=spec.get("value_column", "state_value"),
        updated_at_column=spec.get("updated_at_column", "updated_at"),
        auto_create=spec.get("auto_create", True),
    )


def _build_mysql_cursor_store(ctx: ResourceBuildContext, spec: Mapping[str, Any]) -> Any:
    connector = ctx.resolve_dependency(spec, "connector")
    if not hasattr(connector, "cursor_store"):
        raise TypeError(f"resource {spec['connector']!r} cannot build mysql_cursor_store")
    return connector.cursor_store(
        table=spec.get("table", "onestep_cursor"),
        key_column=spec.get("key_column", "cursor_key"),
        value_column=spec.get("value_column", "cursor_value"),
        updated_at_column=spec.get("updated_at_column", "updated_at"),
        auto_create=spec.get("auto_create", True),
    )


def _build_mysql_table_queue(ctx: ResourceBuildContext, spec: Mapping[str, Any]) -> Any:
    connector = ctx.resolve_dependency(spec, "connector")
    if not hasattr(connector, "table_queue"):
        raise TypeError(f"resource {spec['connector']!r} cannot build mysql_table_queue")
    return connector.table_queue(
        table=ctx.require_string(spec, "table"),
        key=ctx.require_string(spec, "key"),
        where=ctx.require_string(spec, "where"),
        claim=ctx.require_mapping(spec, "claim"),
        ack=ctx.require_mapping(spec, "ack"),
        nack=ctx.optional_mapping(spec.get("nack"), field=f"{ctx.field}.nack") or None,
        batch_size=spec.get("batch_size", 100),
        poll_interval_s=spec.get("poll_interval_s", 1.0),
    )


def _build_mysql_incremental(ctx: ResourceBuildContext, spec: Mapping[str, Any]) -> Any:
    connector = ctx.resolve_dependency(spec, "connector")
    if not hasattr(connector, "incremental"):
        raise TypeError(f"resource {spec['connector']!r} cannot build mysql_incremental")
    raw_state_name = spec.get("state")
    state = None
    if raw_state_name is not None:
        state_name = ctx.string_value(raw_state_name, field=f"{ctx.field}.state")
        state = ctx.resolve(state_name)
        if not ctx.is_cursor_store(state):
            raise TypeError(f"resource {state_name!r} cannot be used as incremental state")
    return connector.incremental(
        table=ctx.require_string(spec, "table"),
        key=ctx.require_string(spec, "key"),
        cursor=tuple(ctx.string_list(spec.get("cursor"), field=f"{ctx.field}.cursor")),
        where=spec.get("where"),
        batch_size=spec.get("batch_size", 1000),
        poll_interval_s=spec.get("poll_interval_s", 1.0),
        state=state,
        state_key=spec.get("state_key"),
    )


def _build_mysql_table_sink(ctx: ResourceBuildContext, spec: Mapping[str, Any]) -> Any:
    connector = ctx.resolve_dependency(spec, "connector")
    if not hasattr(connector, "table_sink"):
        raise TypeError(f"resource {spec['connector']!r} cannot build mysql_table_sink")
    keys = spec.get("keys")
    return connector.table_sink(
        table=ctx.require_string(spec, "table"),
        mode=spec.get("mode", "insert"),
        keys=tuple(ctx.string_list(keys, field=f"{ctx.field}.keys")) if keys is not None else (),
    )
