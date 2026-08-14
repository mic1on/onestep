from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from onestep.resource_registry import (
    ResourceCatalogEntry,
    ResourceCatalogField,
    ResourceBuildContext,
    ResourceRegistry,
    ResourceSpecHandler,
    ResourceValidationContext,
)

from .connector import DEFAULT_FALLBACK_SCAN_PAGE_LIMIT, FeishuBitableConnector

_FEISHU_BITABLE_FIELDS = frozenset({"type", "app_id", "app_secret", "base_url", "timeout_s"})
_FEISHU_INCREMENTAL_FIELDS = frozenset(
    {
        "type",
        "connector",
        "app_token",
        "table_id",
        "cursor_field",
        "batch_size",
        "poll_interval_s",
        "fallback_scan_page_limit",
        "state",
        "state_key",
        "user_id_type",
    }
)
_FEISHU_TABLE_SINK_FIELDS = frozenset(
    {"type", "connector", "app_token", "table_id", "mode", "match_fields", "user_id_type", "relations", "batch_size", "flush_interval_s"}
)
_USER_ID_TYPES = frozenset({"open_id", "union_id", "user_id"})
_RELATION_FIELDS = frozenset({"from", "app_token", "table_id", "key", "on_missing", "create_fields"})
_RELATION_MISSING_POLICIES = frozenset({"error", "empty", "create"})
_FEISHU_BITABLE_CATALOG = ResourceCatalogEntry(
    type="feishu_bitable",
    roles=("connector",),
    label="Feishu Bitable",
    fields=(
        ResourceCatalogField("app_id", "string", required=True),
        ResourceCatalogField("app_secret", "string", required=True, secret=True),
        ResourceCatalogField("base_url", "string", default="https://open.feishu.cn"),
        ResourceCatalogField("timeout_s", "number", default=10.0),
    ),
)
_FEISHU_INCREMENTAL_CATALOG = ResourceCatalogEntry(
    type="feishu_bitable_incremental",
    roles=("source",),
    label="Feishu Bitable Incremental",
    connector_types=("feishu_bitable",),
    fields=(
        ResourceCatalogField("connector", "ref", required=True),
        ResourceCatalogField("app_token", "string", required=True, secret=True),
        ResourceCatalogField("table_id", "string", required=True),
        ResourceCatalogField("cursor_field", "string", required=True),
        ResourceCatalogField("batch_size", "integer", default=100),
        ResourceCatalogField("poll_interval_s", "number", default=1.0),
        ResourceCatalogField("fallback_scan_page_limit", "integer", default=DEFAULT_FALLBACK_SCAN_PAGE_LIMIT),
        ResourceCatalogField("state", "ref"),
        ResourceCatalogField("state_key", "string"),
        ResourceCatalogField("user_id_type", "string", options=tuple(sorted(_USER_ID_TYPES))),
    ),
    topology_fields=("app_token", "table_id", "cursor_field", "batch_size", "poll_interval_s"),
)
_FEISHU_TABLE_SINK_CATALOG = ResourceCatalogEntry(
    type="feishu_bitable_table_sink",
    roles=("sink",),
    label="Feishu Bitable Table Sink",
    connector_types=("feishu_bitable",),
    fields=(
        ResourceCatalogField("connector", "ref", required=True),
        ResourceCatalogField("app_token", "string", required=True, secret=True),
        ResourceCatalogField("table_id", "string", required=True),
        ResourceCatalogField("mode", "string", default="upsert", options=("upsert", "create", "update", "insert")),
        ResourceCatalogField("match_fields", "string_list", required=True),
        ResourceCatalogField("user_id_type", "string", options=tuple(sorted(_USER_ID_TYPES))),
        ResourceCatalogField("relations", "mapping"),
        ResourceCatalogField("batch_size", "integer", default=1),
        ResourceCatalogField("flush_interval_s", "number", default=1.0),
    ),
    topology_fields=("app_token", "table_id", "mode", "match_fields", "batch_size"),
)


def register_resources(registry: ResourceRegistry) -> None:
    registry.register_resource_type(
        ResourceSpecHandler(
            type="feishu_bitable",
            catalog=_FEISHU_BITABLE_CATALOG,
            allowed_fields=_FEISHU_BITABLE_FIELDS,
            build=_build_feishu_bitable,
            validate=_validate_feishu_bitable,
        )
    )
    registry.register_resource_type(
        ResourceSpecHandler(
            type="feishu_bitable_incremental",
            catalog=_FEISHU_INCREMENTAL_CATALOG,
            allowed_fields=_FEISHU_INCREMENTAL_FIELDS,
            build=_build_feishu_bitable_incremental,
            validate=_validate_feishu_bitable_incremental,
        )
    )
    registry.register_resource_type(
        ResourceSpecHandler(
            type="feishu_bitable_table_sink",
            catalog=_FEISHU_TABLE_SINK_CATALOG,
            allowed_fields=_FEISHU_TABLE_SINK_FIELDS,
            build=_build_feishu_bitable_table_sink,
            validate=_validate_feishu_bitable_table_sink,
        )
    )


def _build_feishu_bitable(ctx: ResourceBuildContext, spec: Mapping[str, Any]) -> FeishuBitableConnector:
    return FeishuBitableConnector(
        app_id=ctx.require_string(spec, "app_id"),
        app_secret=ctx.require_string(spec, "app_secret"),
        base_url=spec.get("base_url", "https://open.feishu.cn"),
        timeout_s=spec.get("timeout_s", 10.0),
    )


def _build_feishu_bitable_incremental(ctx: ResourceBuildContext, spec: Mapping[str, Any]) -> Any:
    connector = ctx.resolve_dependency(spec, "connector")
    if not isinstance(connector, FeishuBitableConnector):
        raise TypeError(f"resource {spec['connector']!r} cannot build feishu_bitable_incremental")
    raw_state_name = spec.get("state")
    state = None
    if raw_state_name is not None:
        state_name = ctx.string_value(raw_state_name, field=f"{ctx.field}.state")
        state = ctx.resolve(state_name)
        if not ctx.is_cursor_store(state):
            raise TypeError(f"resource {state_name!r} cannot be used as incremental state")
    return connector.incremental(
        app_token=ctx.require_string(spec, "app_token"),
        table_id=ctx.require_string(spec, "table_id"),
        cursor_field=ctx.require_string(spec, "cursor_field"),
        user_id_type=spec.get("user_id_type"),
        batch_size=spec.get("batch_size", 100),
        poll_interval_s=spec.get("poll_interval_s", 1.0),
        fallback_scan_page_limit=spec.get("fallback_scan_page_limit", DEFAULT_FALLBACK_SCAN_PAGE_LIMIT),
        state=state,
        state_key=spec.get("state_key"),
    )


def _build_feishu_bitable_table_sink(ctx: ResourceBuildContext, spec: Mapping[str, Any]) -> Any:
    connector = ctx.resolve_dependency(spec, "connector")
    if not isinstance(connector, FeishuBitableConnector):
        raise TypeError(f"resource {spec['connector']!r} cannot build feishu_bitable_table_sink")
    return connector.table_sink(
        app_token=ctx.require_string(spec, "app_token"),
        table_id=ctx.require_string(spec, "table_id"),
        mode=spec.get("mode", "upsert"),
        match_fields=spec.get("match_fields"),
        user_id_type=spec.get("user_id_type"),
        relations=spec.get("relations"),
        batch_size=spec.get("batch_size", 1),
        flush_interval_s=spec.get("flush_interval_s", 1.0),
    )


def _validate_feishu_bitable(ctx: ResourceValidationContext, spec: Mapping[str, Any]) -> None:
    ctx.require_string(spec, "app_id")
    ctx.require_string(spec, "app_secret")
    if "base_url" in spec:
        ctx.string_value(spec.get("base_url"), field=f"{ctx.field}.base_url")
    ctx.validate_positive_number(spec.get("timeout_s"), field=f"{ctx.field}.timeout_s")


def _validate_feishu_bitable_incremental(ctx: ResourceValidationContext, spec: Mapping[str, Any]) -> None:
    ctx.require_string(spec, "connector")
    ctx.require_string(spec, "app_token")
    ctx.require_string(spec, "table_id")
    ctx.require_string(spec, "cursor_field")
    ctx.validate_positive_integer(spec.get("batch_size"), field=f"{ctx.field}.batch_size")
    ctx.validate_non_negative_number(spec.get("poll_interval_s"), field=f"{ctx.field}.poll_interval_s")
    if "fallback_scan_page_limit" in spec and spec.get("fallback_scan_page_limit") is None:
        raise ValueError(f"'{ctx.field}.fallback_scan_page_limit' must be >= 1")
    ctx.validate_positive_integer(
        spec.get("fallback_scan_page_limit"),
        field=f"{ctx.field}.fallback_scan_page_limit",
    )
    if "state" in spec:
        ctx.string_value(spec.get("state"), field=f"{ctx.field}.state")
    if "state_key" in spec:
        ctx.string_value(spec.get("state_key"), field=f"{ctx.field}.state_key")
    _validate_feishu_user_id_type(ctx, spec.get("user_id_type"), field=f"{ctx.field}.user_id_type")


def _validate_feishu_bitable_table_sink(ctx: ResourceValidationContext, spec: Mapping[str, Any]) -> None:
    ctx.require_string(spec, "connector")
    ctx.require_string(spec, "app_token")
    ctx.require_string(spec, "table_id")
    raw_mode = spec.get("mode", "upsert")
    mode = ctx.string_value(raw_mode, field=f"{ctx.field}.mode").strip().lower()
    if mode not in {"upsert", "create", "update", "insert"}:
        raise ValueError(f"unsupported {ctx.field}.mode {raw_mode!r}")
    match_fields: list[str] = []
    if mode in {"upsert", "update", "insert"} or "match_fields" in spec:
        match_fields = ctx.require_non_empty_string_list(spec, "match_fields", field=f"{ctx.field}.match_fields")
    _validate_feishu_user_id_type(ctx, spec.get("user_id_type"), field=f"{ctx.field}.user_id_type")
    if "relations" in spec:
        _validate_feishu_relations(
            ctx,
            spec.get("relations"),
            match_fields=match_fields,
            field=f"{ctx.field}.relations",
        )
    ctx.validate_positive_integer(spec.get("batch_size"), field=f"{ctx.field}.batch_size")
    ctx.validate_positive_number(spec.get("flush_interval_s"), field=f"{ctx.field}.flush_interval_s")


def _validate_feishu_relations(
    ctx: ResourceValidationContext,
    value: Any,
    *,
    match_fields: list[str],
    field: str,
) -> None:
    if not isinstance(value, Mapping):
        raise TypeError(f"'{field}' must be a mapping")
    if not value:
        raise ValueError(f"'{field}' must be a non-empty mapping")
    for raw_target_field, raw_config in value.items():
        target_field = ctx.string_value(raw_target_field, field=f"{field} target field").strip()
        relation_field = f"{field}.{target_field}"
        if not isinstance(raw_config, Mapping):
            raise TypeError(f"'{relation_field}' must be a mapping")
        ctx.validate_unknown_fields(raw_config, _RELATION_FIELDS, field=relation_field)
        source_field = ctx.string_value(
            raw_config.get("from", target_field),
            field=f"{relation_field}.from",
        ).strip()
        if "app_token" in raw_config:
            ctx.string_value(raw_config.get("app_token"), field=f"{relation_field}.app_token")
        ctx.string_value(raw_config.get("table_id"), field=f"{relation_field}.table_id")
        key = ctx.string_value(raw_config.get("key"), field=f"{relation_field}.key").strip()
        on_missing = ctx.string_value(
            raw_config.get("on_missing", "error"),
            field=f"{relation_field}.on_missing",
        ).strip().lower()
        if on_missing not in _RELATION_MISSING_POLICIES:
            raise ValueError(
                f"'{relation_field}.on_missing' must be one of 'error', 'empty', or 'create'"
            )
        if "create_fields" in raw_config:
            create_fields = raw_config.get("create_fields")
            if not isinstance(create_fields, Mapping):
                raise TypeError(f"'{relation_field}.create_fields' must be a mapping")
            if any(
                not isinstance(field_name, str) or not field_name.strip()
                for field_name in create_fields
            ):
                raise ValueError(
                    f"'{relation_field}.create_fields' keys must be non-empty strings"
                )
            if on_missing != "create":
                raise ValueError(f"'{relation_field}.create_fields' requires on_missing 'create'")
            if key in create_fields:
                raise ValueError(
                    f"'{relation_field}.create_fields' must not contain relation key {key!r}"
                )
        if target_field in match_fields:
            raise ValueError(f"relation target field {target_field!r} must not appear in match_fields")
        if source_field != target_field and source_field in match_fields:
            raise ValueError(f"relation source field {source_field!r} must not appear in match_fields")


def _validate_feishu_user_id_type(ctx: ResourceValidationContext, value: Any, *, field: str) -> None:
    if value is None:
        return
    normalized = ctx.string_value(value, field=field).strip().lower()
    if normalized not in _USER_ID_TYPES:
        raise ValueError(f"unsupported {field} {value!r}")
