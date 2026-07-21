from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from onestep.resource_registry import (
    ResourceCatalogEntry,
    ResourceCatalogField,
    ResourceBuildContext,
    ResourceRegistry,
    ResourceSpecHandler,
)

from .connector import PostgresConnector

_POSTGRES_FIELDS = frozenset({"type", "dsn", "engine_options"})
_POSTGRES_STATE_STORE_FIELDS = frozenset(
    {"type", "connector", "table", "key_column", "value_column", "updated_at_column", "auto_create"}
)
_POSTGRES_CURSOR_STORE_FIELDS = frozenset(
    {"type", "connector", "table", "key_column", "value_column", "updated_at_column", "auto_create"}
)
_POSTGRES_TABLE_QUEUE_FIELDS = frozenset(
    {"type", "connector", "table", "key", "where", "claim", "ack", "nack", "batch_size", "poll_interval_s"}
)
_POSTGRES_INCREMENTAL_FIELDS = frozenset(
    {"type", "connector", "table", "key", "cursor", "where", "batch_size", "poll_interval_s", "state", "state_key"}
)
_POSTGRES_TABLE_SINK_FIELDS = frozenset({"type", "connector", "table", "mode", "keys"})
_POSTGRES_CATALOG = ResourceCatalogEntry(
    type="postgres",
    roles=("connector",),
    label="Postgres",
    fields=(
        ResourceCatalogField("dsn", "string", required=True, secret=True),
        ResourceCatalogField("engine_options", "mapping"),
        ResourceCatalogField("host", "string"),
        ResourceCatalogField("port", "string"),
        ResourceCatalogField("database", "string"),
        ResourceCatalogField("username", "string"),
        ResourceCatalogField("password", "string", secret=True),
    ),
)
_POSTGRES_STATE_STORE_CATALOG = ResourceCatalogEntry(
    type="postgres_state_store",
    roles=("state_store",),
    label="Postgres State Store",
    connector_types=("postgres",),
    fields=(
        ResourceCatalogField("connector", "ref", required=True),
        ResourceCatalogField("table", "string", default="onestep_state"),
        ResourceCatalogField("key_column", "string", default="state_key"),
        ResourceCatalogField("value_column", "string", default="state_value"),
        ResourceCatalogField("updated_at_column", "string", default="updated_at"),
        ResourceCatalogField("auto_create", "boolean", default=True),
    ),
    topology_fields=("table", "key_column"),
)
_POSTGRES_CURSOR_STORE_CATALOG = ResourceCatalogEntry(
    type="postgres_cursor_store",
    roles=("cursor_store",),
    label="Postgres Cursor Store",
    connector_types=("postgres",),
    fields=(
        ResourceCatalogField("connector", "ref", required=True),
        ResourceCatalogField("table", "string", default="onestep_cursor"),
        ResourceCatalogField("key_column", "string", default="cursor_key"),
        ResourceCatalogField("value_column", "string", default="cursor_value"),
        ResourceCatalogField("updated_at_column", "string", default="updated_at"),
        ResourceCatalogField("auto_create", "boolean", default=True),
    ),
    topology_fields=("table", "key_column"),
)
_POSTGRES_TABLE_QUEUE_CATALOG = ResourceCatalogEntry(
    type="postgres_table_queue",
    roles=("source",),
    label="Postgres Table Queue",
    connector_types=("postgres",),
    fields=(
        ResourceCatalogField("connector", "ref", required=True),
        ResourceCatalogField("table", "string", required=True),
        ResourceCatalogField("key", "string", required=True),
        ResourceCatalogField("where", "string", required=True),
        ResourceCatalogField("claim", "mapping", required=True),
        ResourceCatalogField("ack", "mapping", required=True),
        ResourceCatalogField("nack", "mapping"),
        ResourceCatalogField("batch_size", "integer", default=100),
        ResourceCatalogField("poll_interval_s", "number", default=1.0),
    ),
    topology_fields=("table", "key", "batch_size", "poll_interval_s"),
)
_POSTGRES_INCREMENTAL_CATALOG = ResourceCatalogEntry(
    type="postgres_incremental",
    roles=("source",),
    label="Postgres Incremental",
    connector_types=("postgres",),
    fields=(
        ResourceCatalogField("connector", "ref", required=True),
        ResourceCatalogField("table", "string", required=True),
        ResourceCatalogField("key", "string", required=True),
        ResourceCatalogField("cursor", "string_list", required=True),
        ResourceCatalogField("where", "string"),
        ResourceCatalogField("batch_size", "integer", default=1000),
        ResourceCatalogField("poll_interval_s", "number", default=1.0),
        ResourceCatalogField("state", "ref"),
        ResourceCatalogField("state_key", "string"),
    ),
    topology_fields=("table", "key", "cursor", "batch_size", "poll_interval_s"),
)
_POSTGRES_TABLE_SINK_CATALOG = ResourceCatalogEntry(
    type="postgres_table_sink",
    roles=("sink",),
    label="Postgres Table Sink",
    connector_types=("postgres",),
    fields=(
        ResourceCatalogField("connector", "ref", required=True),
        ResourceCatalogField("table", "string", required=True),
        ResourceCatalogField("mode", "string", default="insert", options=("insert", "upsert")),
        ResourceCatalogField("keys", "string_list"),
    ),
    topology_fields=("table", "mode", "keys"),
)


def register_resources(registry: ResourceRegistry) -> None:
    registry.register_resource_type(
        ResourceSpecHandler(
            type="postgres",
            catalog=_POSTGRES_CATALOG,
            allowed_fields=_POSTGRES_FIELDS,
            build=_build_postgres,
        )
    )
    registry.register_resource_type(
        ResourceSpecHandler(
            type="postgres_state_store",
            catalog=_POSTGRES_STATE_STORE_CATALOG,
            allowed_fields=_POSTGRES_STATE_STORE_FIELDS,
            build=_build_postgres_state_store,
        )
    )
    registry.register_resource_type(
        ResourceSpecHandler(
            type="postgres_cursor_store",
            catalog=_POSTGRES_CURSOR_STORE_CATALOG,
            allowed_fields=_POSTGRES_CURSOR_STORE_FIELDS,
            build=_build_postgres_cursor_store,
        )
    )
    registry.register_resource_type(
        ResourceSpecHandler(
            type="postgres_table_queue",
            catalog=_POSTGRES_TABLE_QUEUE_CATALOG,
            allowed_fields=_POSTGRES_TABLE_QUEUE_FIELDS,
            build=_build_postgres_table_queue,
        )
    )
    registry.register_resource_type(
        ResourceSpecHandler(
            type="postgres_incremental",
            catalog=_POSTGRES_INCREMENTAL_CATALOG,
            allowed_fields=_POSTGRES_INCREMENTAL_FIELDS,
            build=_build_postgres_incremental,
        )
    )
    registry.register_resource_type(
        ResourceSpecHandler(
            type="postgres_table_sink",
            catalog=_POSTGRES_TABLE_SINK_CATALOG,
            allowed_fields=_POSTGRES_TABLE_SINK_FIELDS,
            build=_build_postgres_table_sink,
        )
    )


def _build_postgres(ctx: ResourceBuildContext, spec: Mapping[str, Any]) -> PostgresConnector:
    return PostgresConnector(
        ctx.require_string(spec, "dsn"),
        **ctx.mapping_value(spec.get("engine_options"), field=f"{ctx.field}.engine_options"),
    )


def _build_postgres_state_store(ctx: ResourceBuildContext, spec: Mapping[str, Any]) -> Any:
    connector = ctx.resolve_dependency(spec, "connector")
    if not hasattr(connector, "state_store"):
        raise TypeError(f"resource {spec['connector']!r} cannot build postgres_state_store")
    return connector.state_store(
        table=spec.get("table", "onestep_state"),
        key_column=spec.get("key_column", "state_key"),
        value_column=spec.get("value_column", "state_value"),
        updated_at_column=spec.get("updated_at_column", "updated_at"),
        auto_create=spec.get("auto_create", True),
    )


def _build_postgres_cursor_store(ctx: ResourceBuildContext, spec: Mapping[str, Any]) -> Any:
    connector = ctx.resolve_dependency(spec, "connector")
    if not hasattr(connector, "cursor_store"):
        raise TypeError(f"resource {spec['connector']!r} cannot build postgres_cursor_store")
    return connector.cursor_store(
        table=spec.get("table", "onestep_cursor"),
        key_column=spec.get("key_column", "cursor_key"),
        value_column=spec.get("value_column", "cursor_value"),
        updated_at_column=spec.get("updated_at_column", "updated_at"),
        auto_create=spec.get("auto_create", True),
    )


def _build_postgres_table_queue(ctx: ResourceBuildContext, spec: Mapping[str, Any]) -> Any:
    connector = ctx.resolve_dependency(spec, "connector")
    if not hasattr(connector, "table_queue"):
        raise TypeError(f"resource {spec['connector']!r} cannot build postgres_table_queue")
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


def _build_postgres_incremental(ctx: ResourceBuildContext, spec: Mapping[str, Any]) -> Any:
    connector = ctx.resolve_dependency(spec, "connector")
    if not hasattr(connector, "incremental"):
        raise TypeError(f"resource {spec['connector']!r} cannot build postgres_incremental")
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


def _build_postgres_table_sink(ctx: ResourceBuildContext, spec: Mapping[str, Any]) -> Any:
    connector = ctx.resolve_dependency(spec, "connector")
    if not hasattr(connector, "table_sink"):
        raise TypeError(f"resource {spec['connector']!r} cannot build postgres_table_sink")
    keys = spec.get("keys")
    return connector.table_sink(
        table=ctx.require_string(spec, "table"),
        mode=spec.get("mode", "insert"),
        keys=tuple(ctx.string_list(keys, field=f"{ctx.field}.keys")) if keys is not None else (),
    )
