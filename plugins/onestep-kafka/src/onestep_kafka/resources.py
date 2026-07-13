from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from onestep.resource_registry import ResourceBuildContext, ResourceRegistry, ResourceSpecHandler, ResourceValidationContext

from .connector import KafkaConnector

_KAFKA_FIELDS = frozenset({"type", "bootstrap_servers", "options"})
_KAFKA_TOPIC_FIELDS = frozenset(
    {
        "type",
        "name",
        "connector",
        "topic",
        "group_id",
        "client_id",
        "batch_size",
        "poll_timeout_ms",
        "key",
        "headers",
        "consumer_options",
        "producer_options",
    }
)


def register_resources(registry: ResourceRegistry) -> None:
    registry.register_resource_type(
        ResourceSpecHandler(
            type="kafka",
            allowed_fields=_KAFKA_FIELDS,
            build=_build_kafka,
            validate=_validate_kafka,
        )
    )
    registry.register_resource_type(
        ResourceSpecHandler(
            type="kafka_topic",
            allowed_fields=_KAFKA_TOPIC_FIELDS,
            build=_build_kafka_topic,
            validate=_validate_kafka_topic,
        )
    )


def _build_kafka(ctx: ResourceBuildContext, spec: Mapping[str, Any]) -> KafkaConnector:
    return KafkaConnector(
        _string_or_string_list(spec.get("bootstrap_servers"), field=f"{ctx.field}.bootstrap_servers"),
        options=ctx.mapping_value(spec.get("options"), field=f"{ctx.field}.options"),
    )


def _build_kafka_topic(ctx: ResourceBuildContext, spec: Mapping[str, Any]) -> Any:
    connector = ctx.resolve_dependency(spec, "connector")
    if not hasattr(connector, "topic"):
        raise TypeError(f"resource {spec['connector']!r} cannot build kafka_topic")
    return connector.topic(
        ctx.resource_name(spec, key="topic"),
        group_id=spec.get("group_id"),
        client_id=spec.get("client_id"),
        batch_size=spec.get("batch_size", 100),
        poll_timeout_ms=spec.get("poll_timeout_ms", 1000),
        key=spec.get("key"),
        headers=spec.get("headers"),
        consumer_options=ctx.mapping_value(spec.get("consumer_options"), field=f"{ctx.field}.consumer_options"),
        producer_options=ctx.mapping_value(spec.get("producer_options"), field=f"{ctx.field}.producer_options"),
    )


def _validate_kafka(ctx: ResourceValidationContext, spec: Mapping[str, Any]) -> None:
    _string_or_string_list(spec.get("bootstrap_servers"), field=f"{ctx.field}.bootstrap_servers")
    if "options" in spec and not isinstance(spec.get("options"), Mapping):
        raise TypeError(f"'{ctx.field}.options' must be a mapping")


def _validate_kafka_topic(ctx: ResourceValidationContext, spec: Mapping[str, Any]) -> None:
    ctx.require_string(spec, "connector")
    ctx.require_string(spec, "topic")
    if "group_id" in spec and spec.get("group_id") is not None:
        ctx.string_value(spec.get("group_id"), field=f"{ctx.field}.group_id")
    if "client_id" in spec and spec.get("client_id") is not None:
        ctx.string_value(spec.get("client_id"), field=f"{ctx.field}.client_id")
    if "batch_size" in spec:
        _positive_integer(spec.get("batch_size"), field=f"{ctx.field}.batch_size")
    if "poll_timeout_ms" in spec:
        _non_negative_integer(spec.get("poll_timeout_ms"), field=f"{ctx.field}.poll_timeout_ms")
    if "headers" in spec:
        _validate_headers(spec.get("headers"), field=f"{ctx.field}.headers")
    for key in ("consumer_options", "producer_options"):
        if key in spec and not isinstance(spec.get(key), Mapping):
            raise TypeError(f"'{ctx.field}.{key}' must be a mapping")


def _string_or_string_list(value: Any, *, field: str) -> str | list[str]:
    if isinstance(value, str) and value.strip():
        return value
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        items = [item for item in value if isinstance(item, str) and item.strip()]
        if len(items) == len(value) and items:
            return list(items)
    raise ValueError(f"'{field}' must be a non-empty string or list of strings")


def _positive_integer(value: Any, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"'{field}' must be a positive integer")
    return value


def _non_negative_integer(value: Any, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"'{field}' must be a non-negative integer")
    return value


def _validate_headers(value: Any, *, field: str) -> None:
    if value is None:
        return
    if isinstance(value, Mapping):
        for key in value:
            if not isinstance(key, str) or not key:
                raise ValueError(f"'{field}' header names must be non-empty strings")
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for index, item in enumerate(value):
            if not isinstance(item, Sequence) or isinstance(item, (str, bytes)) or len(item) != 2:
                raise TypeError(f"'{field}[{index}]' must be a two-item header pair")
            if not isinstance(item[0], str) or not item[0]:
                raise ValueError(f"'{field}[{index}][0]' must be a non-empty string")
        return
    raise TypeError(f"'{field}' must be a mapping or list of header pairs")
