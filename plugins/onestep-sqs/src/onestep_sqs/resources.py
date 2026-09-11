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

from .connector import SQSConnector
from .sns import SNSConnector

_SQS_FIELDS = frozenset({"type", "region_name", "options"})
_SNS_FIELDS = frozenset({"type", "region_name", "options"})
_SNS_TOPIC_FIELDS = frozenset(
    {
        "type",
        "name",
        "arn",
        "connector",
        "subject",
        "message_group_id",
        "deduplication_id_factory",
        "message_attributes",
        "retry_delay_s",
    }
)
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
_SQS_CATALOG = ResourceCatalogEntry(
    type="sqs",
    roles=("connector",),
    label="Amazon SQS",
    fields=(
        ResourceCatalogField("region_name", "string"),
        ResourceCatalogField("options", "mapping", secret=True),
    ),
)
_SQS_QUEUE_CATALOG = ResourceCatalogEntry(
    type="sqs_queue",
    roles=("source", "sink"),
    label="SQS Queue",
    connector_types=("sqs",),
    fields=(
        ResourceCatalogField("name", "string"),
        ResourceCatalogField("url", "string", required=True, secret=True),
        ResourceCatalogField("connector", "ref", required=True),
        ResourceCatalogField("wait_time_s", "integer", default=20),
        ResourceCatalogField("visibility_timeout", "integer"),
        ResourceCatalogField("batch_size", "integer", default=10),
        ResourceCatalogField("poll_interval_s", "number", default=0.0),
        ResourceCatalogField("message_group_id", "string"),
        ResourceCatalogField("deduplication_id_factory", "ref"),
        ResourceCatalogField("on_fail", "string", default="leave", options=("leave", "release")),
        ResourceCatalogField("delete_batch_size", "integer", default=10),
        ResourceCatalogField("delete_flush_interval_s", "number", default=0.5),
        ResourceCatalogField("heartbeat_interval_s", "number"),
        ResourceCatalogField("heartbeat_visibility_timeout", "integer"),
    ),
    topology_fields=("url", "wait_time_s", "visibility_timeout", "batch_size", "poll_interval_s"),
)
_SNS_CATALOG = ResourceCatalogEntry(
    type="sns",
    roles=("connector",),
    label="Amazon SNS",
    fields=(
        ResourceCatalogField("region_name", "string"),
        ResourceCatalogField("options", "mapping", secret=True),
    ),
)
_SNS_TOPIC_CATALOG = ResourceCatalogEntry(
    type="sns_topic",
    roles=("sink",),
    label="SNS Topic",
    connector_types=("sns",),
    fields=(
        ResourceCatalogField("name", "string"),
        ResourceCatalogField("arn", "string", required=True, secret=True),
        ResourceCatalogField("connector", "ref", required=True),
        ResourceCatalogField("subject", "string"),
        ResourceCatalogField("message_group_id", "string"),
        ResourceCatalogField("deduplication_id_factory", "ref"),
        ResourceCatalogField("message_attributes", "mapping"),
        ResourceCatalogField("retry_delay_s", "number", default=5.0),
    ),
    topology_fields=("arn",),
)


def register_resources(registry: ResourceRegistry) -> None:
    registry.register_resource_type(
        ResourceSpecHandler(
            type="sqs",
            catalog=_SQS_CATALOG,
            allowed_fields=_SQS_FIELDS,
            build=_build_sqs,
        )
    )
    registry.register_resource_type(
        ResourceSpecHandler(
            type="sqs_queue",
            catalog=_SQS_QUEUE_CATALOG,
            allowed_fields=_SQS_QUEUE_FIELDS,
            build=_build_sqs_queue,
        )
    )
    registry.register_resource_type(
        ResourceSpecHandler(
            type="sns",
            catalog=_SNS_CATALOG,
            allowed_fields=_SNS_FIELDS,
            build=_build_sns,
        )
    )
    registry.register_resource_type(
        ResourceSpecHandler(
            type="sns_topic",
            catalog=_SNS_TOPIC_CATALOG,
            allowed_fields=_SNS_TOPIC_FIELDS,
            build=_build_sns_topic,
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


def _build_sns(ctx: ResourceBuildContext, spec: Mapping[str, Any]) -> SNSConnector:
    return SNSConnector(
        region_name=spec.get("region_name"),
        options=ctx.mapping_value(spec.get("options"), field=f"{ctx.field}.options"),
    )


def _build_sns_topic(ctx: ResourceBuildContext, spec: Mapping[str, Any]) -> Any:
    connector = ctx.resolve_dependency(spec, "connector")
    if not hasattr(connector, "topic"):
        raise TypeError(f"resource {spec['connector']!r} cannot build sns_topic")
    return connector.topic(
        ctx.require_string(spec, "arn"),
        subject=spec.get("subject"),
        message_group_id=spec.get("message_group_id"),
        deduplication_id_factory=ctx.optional_ref(
            spec.get("deduplication_id_factory"),
            field=f"{ctx.field}.deduplication_id_factory",
        ),
        message_attributes=ctx.mapping_value(
            spec.get("message_attributes"), field=f"{ctx.field}.message_attributes"
        ),
        retry_delay_s=spec.get("retry_delay_s", 5.0),
    )
