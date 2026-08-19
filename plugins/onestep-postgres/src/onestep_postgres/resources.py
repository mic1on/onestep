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
from .execution_source import _validate_execution_source_options

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
_POSTGRES_TABLE_SINK_FIELDS = frozenset(
    {
        "type", "connector", "table", "mode", "keys",
        "update_columns", "update_expr", "serialize_json",
    }
)
_POSTGRES_EXECUTION_SOURCE_FIELDS = frozenset(
    {
        "type",
        "connector",
        "namespace",
        "task_names",
        "table",
        "attempts_table",
        "batch_size",
        "poll_interval_s",
        "lease_duration_s",
        "heartbeat_interval_s",
        "worker_id",
        "auto_create",
        "max_payload_bytes",
        "max_metadata_bytes",
        "max_result_bytes",
        "reclaim_batch_size",
    }
)
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
        ResourceCatalogField("mode", "string", default="insert", options=("insert", "upsert", "update")),
        ResourceCatalogField("keys", "string_list"),
        ResourceCatalogField("update_columns", "json"),
        ResourceCatalogField("update_expr", "mapping"),
        ResourceCatalogField("serialize_json", "string", default="auto", options=("auto", "always", "never")),
    ),
    topology_fields=("table", "mode", "keys", "update_columns", "update_expr", "serialize_json"),
)
_POSTGRES_EXECUTION_SOURCE_CATALOG = ResourceCatalogEntry(
    type="postgres_execution_source",
    roles=("source",),
    label="Postgres Execution Source",
    connector_types=("postgres",),
    fields=(
        ResourceCatalogField("connector", "ref", required=True),
        ResourceCatalogField("namespace", "string", required=True),
        ResourceCatalogField("task_names", "string_list", required=True),
        ResourceCatalogField("table", "string", default="onestep_executions"),
        ResourceCatalogField("attempts_table", "string", default="onestep_execution_attempts"),
        ResourceCatalogField("batch_size", "integer", default=100),
        ResourceCatalogField("poll_interval_s", "number", default=1.0),
        ResourceCatalogField("lease_duration_s", "number", default=90.0),
        ResourceCatalogField("heartbeat_interval_s", "number", default=30.0),
        ResourceCatalogField("worker_id", "string", default="onestep-worker"),
        ResourceCatalogField("auto_create", "boolean", default=True),
        ResourceCatalogField("max_payload_bytes", "integer", default=1024 * 1024),
        ResourceCatalogField("max_metadata_bytes", "integer", default=64 * 1024),
        ResourceCatalogField("max_result_bytes", "integer", default=1024 * 1024),
        ResourceCatalogField("reclaim_batch_size", "integer", default=100),
    ),
    topology_fields=("namespace", "task_names", "batch_size", "poll_interval_s"),
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
    registry.register_resource_type(
        ResourceSpecHandler(
            type="postgres_execution_source",
            catalog=_POSTGRES_EXECUTION_SOURCE_CATALOG,
            allowed_fields=_POSTGRES_EXECUTION_SOURCE_FIELDS,
            build=_build_postgres_execution_source,
            validate=_validate_postgres_execution_source,
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
    update_columns = spec.get("update_columns")
    update_expr = spec.get("update_expr")
    return connector.table_sink(
        table=ctx.require_string(spec, "table"),
        mode=spec.get("mode", "insert"),
        keys=tuple(ctx.string_list(keys, field=f"{ctx.field}.keys")) if keys is not None else (),
        update_columns=_update_columns_value(update_columns, field=f"{ctx.field}.update_columns")
        if update_columns is not None
        else None,
        update_expr=ctx.mapping_value(update_expr, field=f"{ctx.field}.update_expr")
        if update_expr is not None
        else None,
        serialize_json=spec.get("serialize_json", "auto"),
    )


def _update_columns_value(value: Any, *, field: str) -> list[str | Mapping[str, str]]:
    if not isinstance(value, list):
        raise ValueError(f"'{field}' must be a list of column names or {{name, policy}} mappings")
    for entry in value:
        if not isinstance(entry, (str, Mapping)):
            raise ValueError(f"'{field}' entries must be column names or {{name, policy}} mappings")
    return value


def _build_postgres_execution_source(ctx: ResourceBuildContext, spec: Mapping[str, Any]) -> Any:
    connector = ctx.resolve_dependency(spec, "connector")
    if not isinstance(connector, PostgresConnector):
        raise TypeError(f"resource {spec['connector']!r} must be a PostgresConnector")
    backend = connector.execution_backend(
        table=spec.get("table", "onestep_executions"),
        attempts_table=spec.get("attempts_table", "onestep_execution_attempts"),
        auto_create=spec.get("auto_create", True),
        max_payload_bytes=spec.get("max_payload_bytes", 1024 * 1024),
        max_metadata_bytes=spec.get("max_metadata_bytes", 64 * 1024),
        max_result_bytes=spec.get("max_result_bytes", 1024 * 1024),
        reclaim_batch_size=spec.get("reclaim_batch_size", 100),
    )
    return backend.source(
        namespace=ctx.require_string(spec, "namespace"),
        task_names=tuple(
            ctx.string_list(spec.get("task_names"), field=f"{ctx.field}.task_names")
        ),
        batch_size=spec.get("batch_size", 100),
        poll_interval_s=spec.get("poll_interval_s", 1.0),
        lease_duration_s=spec.get("lease_duration_s", 90.0),
        heartbeat_interval_s=spec.get("heartbeat_interval_s", 30.0),
        worker_id=spec.get("worker_id", "onestep-worker"),
    )


def _validate_postgres_execution_source(
    ctx: Any,
    spec: Mapping[str, Any],
) -> None:
    namespace = ctx.require_string(spec, "namespace")
    task_names = ctx.require_non_empty_string_list(
        spec, "task_names", field=f"{ctx.field}.task_names"
    )
    for key in ("table", "attempts_table", "worker_id"):
        if spec.get(key) is not None:
            ctx.string_value(spec[key], field=f"{ctx.field}.{key}")
    batch_size = spec.get("batch_size", 100)
    poll_interval_s = spec.get("poll_interval_s", 1.0)
    lease_duration_s = spec.get("lease_duration_s", 90.0)
    heartbeat_interval_s = spec.get("heartbeat_interval_s", 30.0)
    for key, default in (
        ("max_payload_bytes", 1024 * 1024),
        ("max_metadata_bytes", 64 * 1024),
        ("max_result_bytes", 1024 * 1024),
    ):
        ctx.validate_positive_integer(spec.get(key, default), field=f"{ctx.field}.{key}")
    ctx.validate_positive_integer(
        spec.get("reclaim_batch_size", 100),
        field=f"{ctx.field}.reclaim_batch_size",
    )
    if "auto_create" in spec and not isinstance(spec["auto_create"], bool):
        raise TypeError(f"{ctx.field}.auto_create must be a boolean")
    _validate_execution_source_options(
        namespace=namespace,
        task_names=task_names,
        batch_size=batch_size,
        poll_interval_s=poll_interval_s,
        lease_duration_s=lease_duration_s,
        heartbeat_interval_s=heartbeat_interval_s,
        worker_id=spec.get("worker_id", "onestep-worker"),
        field_prefix=ctx.field,
    )
