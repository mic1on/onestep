from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from onestep.connectors.feishu import FeishuBitableConnector
from onestep.resource_registry import ResourceBuildContext, ResourceRegistry, ResourceSpecHandler, ResourceValidationContext

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
        "state",
        "state_key",
        "user_id_type",
    }
)
_FEISHU_TABLE_SINK_FIELDS = frozenset(
    {"type", "connector", "app_token", "table_id", "mode", "match_fields", "user_id_type"}
)
_USER_ID_TYPES = frozenset({"open_id", "union_id", "user_id"})


def register_resources(registry: ResourceRegistry) -> None:
    registry.register_resource_type(
        ResourceSpecHandler(
            type="feishu_bitable",
            allowed_fields=_FEISHU_BITABLE_FIELDS,
            build=_build_feishu_bitable,
            validate=_validate_feishu_bitable,
        )
    )
    registry.register_resource_type(
        ResourceSpecHandler(
            type="feishu_bitable_incremental",
            allowed_fields=_FEISHU_INCREMENTAL_FIELDS,
            build=_build_feishu_bitable_incremental,
            validate=_validate_feishu_bitable_incremental,
        )
    )
    registry.register_resource_type(
        ResourceSpecHandler(
            type="feishu_bitable_table_sink",
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
    if mode not in {"upsert", "create", "update"}:
        raise ValueError(f"unsupported {ctx.field}.mode {raw_mode!r}")
    if mode in {"upsert", "update"}:
        ctx.require_non_empty_string_list(spec, "match_fields", field=f"{ctx.field}.match_fields")
    elif "match_fields" in spec:
        ctx.require_non_empty_string_list(spec, "match_fields", field=f"{ctx.field}.match_fields")
    _validate_feishu_user_id_type(ctx, spec.get("user_id_type"), field=f"{ctx.field}.user_id_type")


def _validate_feishu_user_id_type(ctx: ResourceValidationContext, value: Any, *, field: str) -> None:
    if value is None:
        return
    normalized = ctx.string_value(value, field=field).strip().lower()
    if normalized not in _USER_ID_TYPES:
        raise ValueError(f"unsupported {field} {value!r}")
