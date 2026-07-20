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

from .connector import RedisConnector

_REDIS_FIELDS = frozenset({"type", "url", "options"})
_REDIS_STREAM_FIELDS = frozenset(
    {
        "type",
        "name",
        "stream",
        "connector",
        "group",
        "consumer",
        "batch_size",
        "poll_interval_s",
        "block_ms",
        "start_id",
        "create_group",
        "maxlen",
        "approximate_trim",
    }
)
_REDIS_CATALOG = ResourceCatalogEntry(
    type="redis",
    roles=("connector",),
    label="Redis",
    fields=(
        ResourceCatalogField("url", "string", required=True, secret=True),
        ResourceCatalogField("options", "mapping"),
        ResourceCatalogField("host", "string"),
        ResourceCatalogField("port", "string"),
        ResourceCatalogField("database", "string"),
        ResourceCatalogField("username", "string"),
        ResourceCatalogField("password", "string", secret=True),
    ),
)
_REDIS_STREAM_CATALOG = ResourceCatalogEntry(
    type="redis_stream",
    roles=("source", "sink"),
    label="Redis Stream",
    connector_types=("redis",),
    fields=(
        ResourceCatalogField("name", "string"),
        ResourceCatalogField("stream", "string"),
        ResourceCatalogField("connector", "ref", required=True),
        ResourceCatalogField("group", "string", default="onestep"),
        ResourceCatalogField("consumer", "string"),
        ResourceCatalogField("batch_size", "integer", default=100),
        ResourceCatalogField("poll_interval_s", "number", default=1.0),
        ResourceCatalogField("block_ms", "integer"),
        ResourceCatalogField("start_id", "string", default="$"),
        ResourceCatalogField("create_group", "boolean", default=True),
        ResourceCatalogField("maxlen", "integer"),
        ResourceCatalogField("approximate_trim", "boolean", default=True),
    ),
    topology_fields=("stream", "group", "consumer", "batch_size", "poll_interval_s"),
)


def register_resources(registry: ResourceRegistry) -> None:
    registry.register_resource_type(
        ResourceSpecHandler(
            type="redis",
            catalog=_REDIS_CATALOG,
            allowed_fields=_REDIS_FIELDS,
            build=_build_redis,
        )
    )
    registry.register_resource_type(
        ResourceSpecHandler(
            type="redis_stream",
            catalog=_REDIS_STREAM_CATALOG,
            allowed_fields=_REDIS_STREAM_FIELDS,
            build=_build_redis_stream,
        )
    )


def _build_redis(ctx: ResourceBuildContext, spec: Mapping[str, Any]) -> RedisConnector:
    return RedisConnector(
        ctx.require_string(spec, "url"),
        options=ctx.mapping_value(spec.get("options"), field=f"{ctx.field}.options"),
    )


def _build_redis_stream(ctx: ResourceBuildContext, spec: Mapping[str, Any]) -> Any:
    connector = ctx.resolve_dependency(spec, "connector")
    if not hasattr(connector, "stream"):
        raise TypeError(f"resource {spec['connector']!r} cannot build redis_stream")
    return connector.stream(
        ctx.resource_name(spec, key="stream"),
        group=spec.get("group", "onestep"),
        consumer=spec.get("consumer"),
        batch_size=spec.get("batch_size", 100),
        poll_interval_s=spec.get("poll_interval_s", 1.0),
        block_ms=spec.get("block_ms"),
        start_id=spec.get("start_id", "$"),
        create_group=spec.get("create_group", True),
        maxlen=spec.get("maxlen"),
        approximate_trim=spec.get("approximate_trim", True),
    )
