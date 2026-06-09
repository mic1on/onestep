from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from onestep.connectors.webhook import BearerAuth, WebhookResponse, WebhookSource
from onestep.resource_registry import ResourceBuildContext, ResourceRegistry, ResourceSpecHandler, ResourceValidationContext

_WEBHOOK_FIELDS = frozenset(
    {
        "type",
        "name",
        "path",
        "methods",
        "host",
        "port",
        "parser",
        "auth",
        "response",
        "max_body_bytes",
        "read_timeout_s",
        "queue_maxsize",
        "batch_size",
        "poll_interval_s",
    }
)
_WEBHOOK_AUTH_FIELDS = frozenset({"type", "token", "header", "scheme", "realm"})
_WEBHOOK_RESPONSE_FIELDS = frozenset({"status_code", "body", "headers"})


def register_resources(registry: ResourceRegistry) -> None:
    registry.register_resource_type(
        ResourceSpecHandler(
            type="webhook",
            allowed_fields=_WEBHOOK_FIELDS,
            build=_build_webhook,
            validate=_validate_webhook,
        )
    )


def _build_webhook(ctx: ResourceBuildContext, spec: Mapping[str, Any]) -> WebhookSource:
    methods = spec.get("methods", ("POST",))
    return WebhookSource(
        path=ctx.require_string(spec, "path"),
        methods=tuple(ctx.string_list(methods, field=f"{ctx.field}.methods")),
        host=spec.get("host", "127.0.0.1"),
        port=spec.get("port", 8080),
        parser=spec.get("parser", "auto"),
        auth=_build_webhook_auth(spec.get("auth")),
        response=_build_webhook_response(ctx, spec.get("response")),
        max_body_bytes=spec.get("max_body_bytes", 1024 * 1024),
        read_timeout_s=spec.get("read_timeout_s", 5.0),
        queue_maxsize=spec.get("queue_maxsize", 1000),
        batch_size=spec.get("batch_size", 100),
        poll_interval_s=spec.get("poll_interval_s", 0.1),
        name=ctx.resource_name(spec),
    )


def _build_webhook_auth(raw_auth: Any) -> BearerAuth | None:
    if raw_auth is None:
        return None
    if isinstance(raw_auth, str):
        return BearerAuth(raw_auth)
    if not isinstance(raw_auth, Mapping):
        raise TypeError("'auth' must be a string or mapping")
    auth_type = _normalize_webhook_type(str(raw_auth.get("type", "bearer")))
    if auth_type != "bearer":
        raise ValueError(f"unsupported webhook auth type {auth_type!r}")
    return BearerAuth(
        _require_mapping_string(raw_auth, "token"),
        header=raw_auth.get("header", "authorization"),
        scheme=raw_auth.get("scheme", "Bearer"),
        realm=raw_auth.get("realm", "webhook"),
    )


def _build_webhook_response(ctx: ResourceBuildContext, raw_response: Any) -> WebhookResponse | None:
    if raw_response is None:
        return None
    if not isinstance(raw_response, Mapping):
        raise TypeError("'response' must be a mapping")
    return WebhookResponse(
        status_code=raw_response.get("status_code", 202),
        body=raw_response.get("body"),
        headers=ctx.mapping_value(raw_response.get("headers"), field="response.headers"),
    )


def _validate_webhook(ctx: ResourceValidationContext, spec: Mapping[str, Any]) -> None:
    raw_auth = spec.get("auth")
    if raw_auth is not None:
        if isinstance(raw_auth, str):
            ctx.string_value(raw_auth, field=f"{ctx.field}.auth")
        elif isinstance(raw_auth, Mapping):
            ctx.validate_unknown_fields(raw_auth, _WEBHOOK_AUTH_FIELDS, field=f"{ctx.field}.auth")
            auth_type = _normalize_webhook_type(str(raw_auth.get("type", "bearer")))
            if auth_type != "bearer":
                raise ValueError(f"unsupported webhook auth type {auth_type!r}")
        else:
            raise TypeError(f"'{ctx.field}.auth' must be a string or mapping")
    raw_response = spec.get("response")
    if raw_response is not None:
        if not isinstance(raw_response, Mapping):
            raise TypeError(f"'{ctx.field}.response' must be a mapping")
        ctx.validate_unknown_fields(raw_response, _WEBHOOK_RESPONSE_FIELDS, field=f"{ctx.field}.response")


def _normalize_webhook_type(value: str) -> str:
    return value.strip().lower().replace("-", "_")


def _require_mapping_string(mapping: Mapping[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"'{key}' must be a non-empty string")
    return value
