from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from onestep.connectors.http import HttpSink
from onestep.resource_registry import ResourceBuildContext, ResourceRegistry, ResourceSpecHandler, ResourceValidationContext

_HTTP_SINK_FIELDS = frozenset(
    {"type", "name", "url", "method", "headers", "params", "body", "timeout_s", "success_statuses"}
)


def register_resources(registry: ResourceRegistry) -> None:
    registry.register_resource_type(
        ResourceSpecHandler(
            type="http_sink",
            allowed_fields=_HTTP_SINK_FIELDS,
            build=_build_http_sink,
            validate=_validate_http_sink,
        )
    )


def _build_http_sink(ctx: ResourceBuildContext, spec: Mapping[str, Any]) -> HttpSink:
    return HttpSink(
        ctx.resource_name(spec),
        url=ctx.require_string(spec, "url"),
        method=spec.get("method", "POST"),
        headers=ctx.mapping_value(spec.get("headers"), field=f"{ctx.field}.headers"),
        params=ctx.mapping_value(spec.get("params"), field=f"{ctx.field}.params"),
        body=spec.get("body"),
        timeout_s=spec.get("timeout_s", 5.0),
        success_statuses=spec.get("success_statuses"),
    )


def _validate_http_sink(ctx: ResourceValidationContext, spec: Mapping[str, Any]) -> None:
    ctx.require_string(spec, "url")
    if "method" in spec:
        ctx.string_value(spec.get("method"), field=f"{ctx.field}.method")
    raw_headers = spec.get("headers")
    if raw_headers is not None and not isinstance(raw_headers, Mapping):
        raise TypeError(f"'{ctx.field}.headers' must be a mapping")
    raw_params = spec.get("params")
    if raw_params is not None and not isinstance(raw_params, Mapping):
        raise TypeError(f"'{ctx.field}.params' must be a mapping")
    _validate_json_like(spec.get("body"), field=f"{ctx.field}.body")
    ctx.validate_positive_number(spec.get("timeout_s"), field=f"{ctx.field}.timeout_s")
    raw_success_statuses = spec.get("success_statuses")
    if raw_success_statuses is not None:
        if not isinstance(raw_success_statuses, Sequence) or isinstance(raw_success_statuses, (str, bytes)):
            raise TypeError(f"'{ctx.field}.success_statuses' must be a list of integers")
        if not raw_success_statuses:
            raise ValueError(f"'{ctx.field}.success_statuses' must not be empty")
        for index, status in enumerate(raw_success_statuses):
            if isinstance(status, bool) or not isinstance(status, int):
                raise TypeError(f"'{ctx.field}.success_statuses[{index}]' must be an integer")
            if status < 100 or status > 599:
                raise ValueError(f"'{ctx.field}.success_statuses[{index}]' must be an HTTP status code")


def _validate_json_like(value: Any, *, field: str) -> None:
    if value is None or isinstance(value, (str, int, float, bool)):
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"'{field}' mapping keys must be strings")
            _validate_json_like(item, field=f"{field}.{key}")
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            _validate_json_like(item, field=f"{field}[{index}]")
        return
    raise TypeError(f"'{field}' must be a JSON-compatible value")
