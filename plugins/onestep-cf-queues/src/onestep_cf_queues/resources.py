from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from onestep.resource_registry import (
    ResourceBuildContext,
    ResourceCatalogEntry,
    ResourceCatalogField,
    ResourceRegistry,
    ResourceSpecHandler,
)

from .connector import CFQueuesConnector

_CF_QUEUES_FIELDS = frozenset(
    {
        "type",
        "account_id",
        "api_token",
        "base_url",
        "timeout_s",
    }
)
_CF_QUEUE_FIELDS = frozenset(
    {
        "type",
        "name",
        "queue_id",
        "connector",
        "batch_size",
        "visibility_timeout_ms",
        "poll_interval_s",
        "on_fail",
        "ack_batch_size",
        "ack_flush_interval_s",
    }
)
_CF_QUEUES_CATALOG = ResourceCatalogEntry(
    type="cf_queues",
    roles=("connector",),
    label="Cloudflare Queues",
    fields=(
        ResourceCatalogField("account_id", "string", required=True),
        ResourceCatalogField("api_token", "string", required=True, secret=True),
        ResourceCatalogField(
            "base_url",
            "string",
        ),
        ResourceCatalogField("timeout_s", "number", default=10.0),
    ),
)
_CF_QUEUE_CATALOG = ResourceCatalogEntry(
    type="cf_queue",
    roles=("source", "sink"),
    label="Cloudflare Queue",
    connector_types=("cf_queues",),
    fields=(
        ResourceCatalogField("name", "string"),
        ResourceCatalogField("queue_id", "string", required=True),
        ResourceCatalogField("connector", "ref", required=True),
        ResourceCatalogField("batch_size", "integer", default=5),
        ResourceCatalogField("visibility_timeout_ms", "integer"),
        ResourceCatalogField("poll_interval_s", "number", default=1.0),
        ResourceCatalogField(
            "on_fail", "string", default="leave", options=("leave", "retry", "ack")
        ),
        ResourceCatalogField("ack_batch_size", "integer", default=100),
        ResourceCatalogField("ack_flush_interval_s", "number", default=0.5),
    ),
    topology_fields=(
        "queue_id",
        "batch_size",
        "visibility_timeout_ms",
        "poll_interval_s",
    ),
)


def register_resources(registry: ResourceRegistry) -> None:
    registry.register_resource_type(
        ResourceSpecHandler(
            type="cf_queues",
            catalog=_CF_QUEUES_CATALOG,
            allowed_fields=_CF_QUEUES_FIELDS,
            build=_build_cf_queues,
        )
    )
    registry.register_resource_type(
        ResourceSpecHandler(
            type="cf_queue",
            catalog=_CF_QUEUE_CATALOG,
            allowed_fields=_CF_QUEUE_FIELDS,
            build=_build_cf_queue,
        )
    )


def _build_cf_queues(ctx: ResourceBuildContext, spec: Mapping[str, Any]) -> CFQueuesConnector:
    return CFQueuesConnector(
        account_id=ctx.require_string(spec, "account_id"),
        api_token=ctx.require_string(spec, "api_token"),
        base_url=spec.get("base_url"),
        timeout_s=spec.get("timeout_s", 10.0),
    )


def _build_cf_queue(ctx: ResourceBuildContext, spec: Mapping[str, Any]) -> Any:
    connector = ctx.resolve_dependency(spec, "connector")
    if not hasattr(connector, "queue"):
        raise TypeError(f"resource {spec['connector']!r} cannot build cf_queue")
    return connector.queue(
        ctx.require_string(spec, "queue_id"),
        batch_size=spec.get("batch_size", 5),
        visibility_timeout_ms=spec.get("visibility_timeout_ms"),
        poll_interval_s=spec.get("poll_interval_s", 1.0),
        on_fail=spec.get("on_fail", "leave"),
        ack_batch_size=spec.get("ack_batch_size", 100),
        ack_flush_interval_s=spec.get("ack_flush_interval_s", 0.5),
    )
