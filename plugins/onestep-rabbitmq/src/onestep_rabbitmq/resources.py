from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from onestep.resource_registry import ResourceBuildContext, ResourceRegistry, ResourceSpecHandler

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


def register_resources(registry: ResourceRegistry) -> None:
    registry.register_resource_type(
        ResourceSpecHandler(
            type="rabbitmq",
            allowed_fields=_RABBITMQ_FIELDS,
            build=_build_rabbitmq,
        )
    )
    registry.register_resource_type(
        ResourceSpecHandler(
            type="rabbitmq_queue",
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
