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

from .connector import RabbitMQConnector

_RABBITMQ_FIELDS = frozenset({"type", "url", "options"})
_RABBITMQ_QUEUE_FIELDS = frozenset(
    {
        "type",
        "name",
        "queue",
        "connector",
        "routing_key",
        "exchange",
        "exchange_type",
        "bind",
        "bind_arguments",
        "durable",
        "auto_delete",
        "exclusive",
        "arguments",
        "exchange_durable",
        "exchange_auto_delete",
        "exchange_arguments",
        "prefetch",
        "batch_size",
        "poll_interval_s",
        "publisher_confirms",
        "persistent",
    }
)
_RABBITMQ_CATALOG = ResourceCatalogEntry(
    type="rabbitmq",
    roles=("connector",),
    label="RabbitMQ",
    fields=(
        ResourceCatalogField("url", "string", required=True, secret=True),
        ResourceCatalogField("options", "mapping"),
        ResourceCatalogField("host", "string"),
        ResourceCatalogField("port", "string"),
        ResourceCatalogField("vhost", "string"),
        ResourceCatalogField("username", "string"),
        ResourceCatalogField("password", "string", secret=True),
    ),
)
_RABBITMQ_QUEUE_CATALOG = ResourceCatalogEntry(
    type="rabbitmq_queue",
    roles=("source", "sink"),
    label="RabbitMQ Queue",
    connector_types=("rabbitmq",),
    fields=(
        ResourceCatalogField("name", "string"),
        ResourceCatalogField("queue", "string"),
        ResourceCatalogField("connector", "ref", required=True),
        ResourceCatalogField("routing_key", "string"),
        ResourceCatalogField("exchange", "string"),
        ResourceCatalogField("exchange_type", "string", default="direct"),
        ResourceCatalogField("bind", "boolean", default=True),
        ResourceCatalogField("bind_arguments", "mapping"),
        ResourceCatalogField("durable", "boolean", default=True),
        ResourceCatalogField("auto_delete", "boolean", default=False),
        ResourceCatalogField("exclusive", "boolean", default=False),
        ResourceCatalogField("arguments", "mapping"),
        ResourceCatalogField("exchange_durable", "boolean"),
        ResourceCatalogField("exchange_auto_delete", "boolean", default=False),
        ResourceCatalogField("exchange_arguments", "mapping"),
        ResourceCatalogField("prefetch", "integer", default=100),
        ResourceCatalogField("batch_size", "integer", default=100),
        ResourceCatalogField("poll_interval_s", "number", default=1.0),
        ResourceCatalogField("publisher_confirms", "boolean", default=True),
        ResourceCatalogField("persistent", "boolean", default=True),
    ),
    topology_fields=("queue", "exchange", "routing_key", "batch_size", "poll_interval_s"),
)


def register_resources(registry: ResourceRegistry) -> None:
    registry.register_resource_type(
        ResourceSpecHandler(
            type="rabbitmq",
            catalog=_RABBITMQ_CATALOG,
            allowed_fields=_RABBITMQ_FIELDS,
            build=_build_rabbitmq,
        )
    )
    registry.register_resource_type(
        ResourceSpecHandler(
            type="rabbitmq_queue",
            catalog=_RABBITMQ_QUEUE_CATALOG,
            allowed_fields=_RABBITMQ_QUEUE_FIELDS,
            build=_build_rabbitmq_queue,
        )
    )


def _build_rabbitmq(ctx: ResourceBuildContext, spec: Mapping[str, Any]) -> RabbitMQConnector:
    return RabbitMQConnector(
        ctx.require_string(spec, "url"),
        options=ctx.mapping_value(spec.get("options"), field=f"{ctx.field}.options"),
    )


def _build_rabbitmq_queue(ctx: ResourceBuildContext, spec: Mapping[str, Any]) -> Any:
    connector = ctx.resolve_dependency(spec, "connector")
    if not hasattr(connector, "queue"):
        raise TypeError(f"resource {spec['connector']!r} cannot build rabbitmq_queue")
    return connector.queue(
        ctx.resource_name(spec, key="queue"),
        routing_key=spec.get("routing_key"),
        exchange=spec.get("exchange"),
        exchange_type=spec.get("exchange_type", "direct"),
        bind=spec.get("bind", True),
        bind_arguments=ctx.mapping_value(spec.get("bind_arguments"), field=f"{ctx.field}.bind_arguments"),
        durable=spec.get("durable", True),
        auto_delete=spec.get("auto_delete", False),
        exclusive=spec.get("exclusive", False),
        arguments=ctx.mapping_value(spec.get("arguments"), field=f"{ctx.field}.arguments"),
        exchange_durable=spec.get("exchange_durable"),
        exchange_auto_delete=spec.get("exchange_auto_delete", False),
        exchange_arguments=ctx.mapping_value(
            spec.get("exchange_arguments"),
            field=f"{ctx.field}.exchange_arguments",
        ),
        prefetch=spec.get("prefetch", 100),
        batch_size=spec.get("batch_size", 100),
        poll_interval_s=spec.get("poll_interval_s", 1.0),
        publisher_confirms=spec.get("publisher_confirms", True),
        persistent=spec.get("persistent", True),
    )
