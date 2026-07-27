from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import parse_qs, urlparse

from onestep import ResourceBuildContext, ResourceCatalogEntry, ResourceCatalogField, ResourceRegistry, ResourceSpecHandler, ResourceValidationContext

from .connector import MongoDBConnector

CONNECTOR_FIELDS = frozenset({"type", "uri", "database", "client_options"})
POLLING_FIELDS = frozenset({"type", "connector", "collection", "cursor", "filter", "projection", "batch_size", "poll_interval_s", "state", "state_key", "initial_cursor"})
CHANGE_FIELDS = frozenset({"type", "connector", "collection", "pipeline", "full_document", "max_await_time_ms", "batch_size", "poll_interval_s", "state", "state_key"})
SINK_FIELDS = frozenset({"type", "connector", "collection", "mode", "keys", "ordered", "batch_size"})

CONNECTOR_CATALOG = ResourceCatalogEntry(
    type="mongodb", roles=("connector",), label="MongoDB",
    fields=(
        ResourceCatalogField("uri", "string", required=True, secret=True),
        ResourceCatalogField("database", "string", required=True),
        ResourceCatalogField("client_options", "mapping", secret=True),
    ),
)
POLLING_CATALOG = ResourceCatalogEntry(
    type="mongodb_polling", roles=("source",), label="MongoDB Polling", connector_types=("mongodb",),
    fields=(
        ResourceCatalogField("connector", "ref", required=True), ResourceCatalogField("collection", "string", required=True),
        ResourceCatalogField("cursor", "string_list", default=["_id"]), ResourceCatalogField("filter", "mapping"),
        ResourceCatalogField("projection", "mapping"), ResourceCatalogField("batch_size", "integer", default=100),
        ResourceCatalogField("poll_interval_s", "number", default=1.0), ResourceCatalogField("state", "ref"),
        ResourceCatalogField("state_key", "string"), ResourceCatalogField("initial_cursor", "json"),
    ), topology_fields=("collection", "cursor", "batch_size", "poll_interval_s"),
)
CHANGE_CATALOG = ResourceCatalogEntry(
    type="mongodb_change_stream", roles=("source",), label="MongoDB Change Stream", connector_types=("mongodb",),
    fields=(
        ResourceCatalogField("connector", "ref", required=True), ResourceCatalogField("collection", "string", required=True),
        ResourceCatalogField("pipeline", "json"), ResourceCatalogField("full_document", "string", default="updateLookup", options=("default", "updateLookup", "whenAvailable", "required")),
        ResourceCatalogField("max_await_time_ms", "integer", default=1000), ResourceCatalogField("batch_size", "integer", default=100),
        ResourceCatalogField("poll_interval_s", "number", default=0.1), ResourceCatalogField("state", "ref"),
        ResourceCatalogField("state_key", "string"),
    ), topology_fields=("collection", "full_document", "batch_size", "max_await_time_ms"),
)
SINK_CATALOG = ResourceCatalogEntry(
    type="mongodb_collection_sink", roles=("sink",), label="MongoDB Collection Sink", connector_types=("mongodb",),
    fields=(
        ResourceCatalogField("connector", "ref", required=True), ResourceCatalogField("collection", "string", required=True),
        ResourceCatalogField("mode", "string", default="insert", options=("insert", "upsert")), ResourceCatalogField("keys", "string_list"),
        ResourceCatalogField("ordered", "boolean", default=True), ResourceCatalogField("batch_size", "integer", default=1000),
    ), topology_fields=("collection", "mode", "keys", "batch_size"),
)


def _unique_strings(ctx: ResourceValidationContext, spec: Mapping[str, Any], key: str, *, default: Sequence[str] | None = None) -> list[str]:
    if key not in spec and default is not None:
        return list(default)
    return ctx.require_non_empty_string_list(spec, key, field=f"{ctx.field}.{key}")


def _validate_connector(ctx: ResourceValidationContext, spec: Mapping[str, Any]) -> None:
    uri = ctx.require_string(spec, "uri")
    ctx.require_string(spec, "database")
    if urlparse(uri).scheme not in {"mongodb", "mongodb+srv"}:
        raise ValueError(f"'{ctx.field}.uri' must be a MongoDB URI")
    options = spec.get("client_options", {})
    if not isinstance(options, Mapping):
        raise TypeError(f"'{ctx.field}.client_options' must be a mapping")
    query = parse_qs(urlparse(uri).query)
    if options.get("w") in {0, "0"} or query.get("w") == ["0"]:
        raise ValueError(f"'{ctx.field}' requires acknowledged write concern")


def _validate_polling(ctx: ResourceValidationContext, spec: Mapping[str, Any]) -> None:
    ctx.require_string(spec, "connector")
    ctx.require_string(spec, "collection")
    cursor = _unique_strings(ctx, spec, "cursor", default=("_id",))
    if "_id" in cursor and cursor[-1] != "_id":
        raise ValueError(f"'{ctx.field}.cursor' requires _id as the final component")
    for key in ("filter", "projection"):
        if key in spec and not isinstance(spec.get(key), Mapping):
            raise TypeError(f"'{ctx.field}.{key}' must be a mapping")
    ctx.validate_positive_integer(spec.get("batch_size"), field=f"{ctx.field}.batch_size")
    ctx.validate_non_negative_number(spec.get("poll_interval_s"), field=f"{ctx.field}.poll_interval_s")
    initial = spec.get("initial_cursor")
    effective_length = len(cursor) if cursor[-1] == "_id" else len(cursor) + 1
    if initial is not None and (not isinstance(initial, Sequence) or isinstance(initial, (str, bytes)) or len(initial) != effective_length):
        raise ValueError(f"'{ctx.field}.initial_cursor' must match the effective cursor length")


def _validate_change(ctx: ResourceValidationContext, spec: Mapping[str, Any]) -> None:
    ctx.require_string(spec, "connector")
    ctx.require_string(spec, "collection")
    pipeline = spec.get("pipeline", [])
    if not isinstance(pipeline, Sequence) or isinstance(pipeline, (str, bytes)) or any(not isinstance(stage, Mapping) for stage in pipeline):
        raise TypeError(f"'{ctx.field}.pipeline' must be a list of mappings")
    if spec.get("full_document", "updateLookup") not in {"default", "updateLookup", "whenAvailable", "required"}:
        raise ValueError(f"'{ctx.field}.full_document' is invalid")
    ctx.validate_positive_integer(spec.get("max_await_time_ms"), field=f"{ctx.field}.max_await_time_ms")
    ctx.validate_positive_integer(spec.get("batch_size"), field=f"{ctx.field}.batch_size")
    ctx.validate_non_negative_number(spec.get("poll_interval_s"), field=f"{ctx.field}.poll_interval_s")


def _validate_sink(ctx: ResourceValidationContext, spec: Mapping[str, Any]) -> None:
    ctx.require_string(spec, "connector")
    ctx.require_string(spec, "collection")
    mode = spec.get("mode", "insert")
    if mode not in {"insert", "upsert"}:
        raise ValueError(f"'{ctx.field}.mode' is invalid")
    if mode == "upsert":
        _unique_strings(ctx, spec, "keys")
    elif "keys" in spec:
        _unique_strings(ctx, spec, "keys")
    ctx.validate_positive_integer(spec.get("batch_size"), field=f"{ctx.field}.batch_size")


def _connector(ctx: ResourceBuildContext, spec: Mapping[str, Any]) -> MongoDBConnector:
    value = ctx.resolve_dependency(spec, "connector")
    if not isinstance(value, MongoDBConnector):
        raise TypeError(f"resource {spec['connector']!r} is not a MongoDBConnector")
    return value


def _state(ctx: ResourceBuildContext, spec: Mapping[str, Any]):
    name = spec.get("state")
    if name is None:
        return None
    resolved_name = ctx.string_value(name, field=f"{ctx.field}.state")
    value = ctx.resolve(resolved_name)
    if not ctx.is_cursor_store(value):
        raise TypeError(f"resource {resolved_name!r} is not a cursor store")
    return value


def _build_connector(ctx: ResourceBuildContext, spec: Mapping[str, Any]) -> MongoDBConnector:
    return MongoDBConnector(ctx.require_string(spec, "uri"), database=ctx.require_string(spec, "database"), client_options=ctx.mapping_value(spec.get("client_options"), field=f"{ctx.field}.client_options"))


def _build_polling(ctx: ResourceBuildContext, spec: Mapping[str, Any]):
    return _connector(ctx, spec).poll_collection(
        ctx.require_string(spec, "collection"), cursor=tuple(ctx.string_list(spec.get("cursor", ["_id"]), field=f"{ctx.field}.cursor")),
        filter=ctx.mapping_value(spec.get("filter"), field=f"{ctx.field}.filter"), projection=ctx.mapping_value(spec.get("projection"), field=f"{ctx.field}.projection"),
        batch_size=spec.get("batch_size", 100), poll_interval_s=spec.get("poll_interval_s", 1.0), state=_state(ctx, spec), state_key=spec.get("state_key"), initial_cursor=spec.get("initial_cursor"),
    )


def _build_change(ctx: ResourceBuildContext, spec: Mapping[str, Any]):
    pipeline = spec.get("pipeline", [])
    return _connector(ctx, spec).watch_collection(
        ctx.require_string(spec, "collection"), pipeline=[dict(stage) for stage in pipeline], full_document=spec.get("full_document", "updateLookup"),
        max_await_time_ms=spec.get("max_await_time_ms", 1000), batch_size=spec.get("batch_size", 100), poll_interval_s=spec.get("poll_interval_s", 0.1),
        state=_state(ctx, spec), state_key=spec.get("state_key"),
    )


def _build_sink(ctx: ResourceBuildContext, spec: Mapping[str, Any]):
    keys = tuple(ctx.string_list(spec.get("keys"), field=f"{ctx.field}.keys")) if spec.get("keys") is not None else ()
    return _connector(ctx, spec).collection_sink(ctx.require_string(spec, "collection"), mode=spec.get("mode", "insert"), keys=keys, ordered=spec.get("ordered", True), batch_size=spec.get("batch_size", 1000))


def register_resources(registry: ResourceRegistry) -> None:
    registry.register_resource_type(ResourceSpecHandler(type="mongodb", catalog=CONNECTOR_CATALOG, allowed_fields=CONNECTOR_FIELDS, build=_build_connector, validate=_validate_connector))
    registry.register_resource_type(ResourceSpecHandler(type="mongodb_polling", catalog=POLLING_CATALOG, allowed_fields=POLLING_FIELDS, build=_build_polling, validate=_validate_polling))
    registry.register_resource_type(ResourceSpecHandler(type="mongodb_change_stream", catalog=CHANGE_CATALOG, allowed_fields=CHANGE_FIELDS, build=_build_change, validate=_validate_change))
    registry.register_resource_type(ResourceSpecHandler(type="mongodb_collection_sink", catalog=SINK_CATALOG, allowed_fields=SINK_FIELDS, build=_build_sink, validate=_validate_sink))
