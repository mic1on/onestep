from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

from onestep import (
    ResourceBuildContext,
    ResourceCatalogEntry,
    ResourceCatalogField,
    ResourceRegistry,
    ResourceSpecHandler,
    ResourceValidationContext,
)

from .connector import ClickHouseConnector

CONNECTOR_FIELDS = frozenset({"type", "dsn", "client_options"})
SINK_FIELDS = frozenset(
    {"type", "connector", "table", "columns", "batch_size", "settings"}
)
CONNECTOR_CATALOG = ResourceCatalogEntry(
    type="clickhouse",
    roles=("connector",),
    label="ClickHouse",
    fields=(
        ResourceCatalogField("dsn", "string", required=True, secret=True),
        ResourceCatalogField("client_options", "mapping", secret=True),
    ),
)
SINK_CATALOG = ResourceCatalogEntry(
    type="clickhouse_table_sink",
    roles=("sink",),
    label="ClickHouse Table Sink",
    connector_types=("clickhouse",),
    fields=(
        ResourceCatalogField("connector", "ref", required=True),
        ResourceCatalogField("table", "string", required=True),
        ResourceCatalogField("columns", "string_list"),
        ResourceCatalogField("batch_size", "integer", default=1000),
        ResourceCatalogField("settings", "mapping"),
    ),
    topology_fields=("table", "columns", "batch_size"),
)


def _validate_connector(
    ctx: ResourceValidationContext, spec: Mapping[str, Any]
) -> None:
    dsn = ctx.require_string(spec, "dsn")
    parsed = urlparse(dsn)
    if parsed.scheme not in {"clickhouse", "clickhouses", "http", "https"} or not parsed.netloc:
        raise ValueError(f"'{ctx.field}.dsn' must be a ClickHouse or HTTP(S) DSN")
    if "client_options" in spec and not isinstance(spec.get("client_options"), Mapping):
        raise TypeError(f"'{ctx.field}.client_options' must be a mapping")


def _validate_sink(ctx: ResourceValidationContext, spec: Mapping[str, Any]) -> None:
    ctx.require_string(spec, "connector")
    ctx.require_string(spec, "table")
    if "columns" in spec:
        ctx.require_non_empty_string_list(
            spec, "columns", field=f"{ctx.field}.columns"
        )
    ctx.validate_positive_integer(
        spec.get("batch_size"), field=f"{ctx.field}.batch_size"
    )
    settings = spec.get("settings", {})
    if not isinstance(settings, Mapping):
        raise TypeError(f"'{ctx.field}.settings' must be a mapping")
    if settings.get("async_insert") in {1, True, "1"} and settings.get(
        "wait_for_async_insert"
    ) not in {1, True, "1"}:
        raise ValueError(
            f"'{ctx.field}.settings.wait_for_async_insert' must be 1 when async_insert is enabled"
        )


def _build_connector(
    ctx: ResourceBuildContext, spec: Mapping[str, Any]
) -> ClickHouseConnector:
    return ClickHouseConnector(
        ctx.require_string(spec, "dsn"),
        client_options=ctx.mapping_value(
            spec.get("client_options"), field=f"{ctx.field}.client_options"
        ),
    )


def _build_sink(ctx: ResourceBuildContext, spec: Mapping[str, Any]):
    connector = ctx.resolve_dependency(spec, "connector")
    if not isinstance(connector, ClickHouseConnector):
        raise TypeError(f"resource {spec['connector']!r} is not a ClickHouseConnector")
    columns = (
        tuple(ctx.string_list(spec.get("columns"), field=f"{ctx.field}.columns"))
        if spec.get("columns") is not None
        else None
    )
    return connector.table_sink(
        table=ctx.require_string(spec, "table"),
        columns=columns,
        batch_size=spec.get("batch_size", 1000),
        settings=ctx.mapping_value(spec.get("settings"), field=f"{ctx.field}.settings"),
    )


def register_resources(registry: ResourceRegistry) -> None:
    registry.register_resource_type(
        ResourceSpecHandler(
            type="clickhouse",
            catalog=CONNECTOR_CATALOG,
            allowed_fields=CONNECTOR_FIELDS,
            build=_build_connector,
            validate=_validate_connector,
        )
    )
    registry.register_resource_type(
        ResourceSpecHandler(
            type="clickhouse_table_sink",
            catalog=SINK_CATALOG,
            allowed_fields=SINK_FIELDS,
            build=_build_sink,
            validate=_validate_sink,
        )
    )
