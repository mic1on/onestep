from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from onestep.connectors.sqs import SQSConnector
from onestep.resource_registry import ResourceBuildContext, ResourceRegistry, ResourceSpecHandler

_SQS_FIELDS = frozenset({"type", "region_name", "options"})
_SQS_QUEUE_FIELDS = frozenset(
    {
        "type",
        "name",
        "url",
        "connector",
        "wait_time_s",
        "visibility_timeout",
        "batch_size",
        "poll_interval_s",
        "message_group_id",
        "deduplication_id_factory",
        "on_fail",
        "delete_batch_size",
        "delete_flush_interval_s",
        "heartbeat_interval_s",
        "heartbeat_visibility_timeout",
    }
)


def register_resources(registry: ResourceRegistry) -> None:
    registry.register_resource_type(
        ResourceSpecHandler(
            type="sqs",
            allowed_fields=_SQS_FIELDS,
            build=_build_sqs,
        )
    )
    registry.register_resource_type(
        ResourceSpecHandler(
            type="sqs_queue",
            allowed_fields=_SQS_QUEUE_FIELDS,
            build=_build_sqs_queue,
        )
    )


def _build_sqs(ctx: ResourceBuildContext, spec: Mapping[str, Any]) -> SQSConnector:
    return SQSConnector(
        region_name=spec.get("region_name"),
        options=ctx.mapping_value(spec.get("options"), field=f"{ctx.field}.options"),
    )


def _build_sqs_queue(ctx: ResourceBuildContext, spec: Mapping[str, Any]) -> Any:
    connector = ctx.resolve_dependency(spec, "connector")
    if not hasattr(connector, "queue"):
        raise TypeError(f"resource {spec['connector']!r} cannot build sqs_queue")
    return connector.queue(
        ctx.require_string(spec, "url"),
        wait_time_s=spec.get("wait_time_s", 20),
        visibility_timeout=spec.get("visibility_timeout"),
        batch_size=spec.get("batch_size", 10),
        poll_interval_s=spec.get("poll_interval_s", 0.0),
        message_group_id=spec.get("message_group_id"),
        deduplication_id_factory=ctx.optional_ref(
            spec.get("deduplication_id_factory"),
            field=f"{ctx.field}.deduplication_id_factory",
        ),
        on_fail=spec.get("on_fail", "leave"),
        delete_batch_size=spec.get("delete_batch_size", 10),
        delete_flush_interval_s=spec.get("delete_flush_interval_s", 0.5),
        heartbeat_interval_s=spec.get("heartbeat_interval_s"),
        heartbeat_visibility_timeout=spec.get("heartbeat_visibility_timeout"),
    )
