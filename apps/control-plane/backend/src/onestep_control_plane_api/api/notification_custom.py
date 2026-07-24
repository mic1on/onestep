from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from onestep_control_plane_api.api.notification_helpers import (
    NotificationEventRecord,
    format_datetime_for_message,
)

CUSTOM_WEBHOOK_VARIABLES = frozenset(
    {
        "event_type",
        "service_name",
        "service_environment",
        "task_name",
        "occurred_at",
        "scheduled_at",
        "duration_ms",
        "attempts",
        "instance_id",
        "node_name",
        "console_url",
        "failure_message",
        "success_summary",
    }
)
_TOKEN_RE = re.compile(r"{{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*}}")


def validate_custom_template_value(value: str) -> None:
    for match in _TOKEN_RE.finditer(value):
        variable_name = match.group(1)
        if variable_name not in CUSTOM_WEBHOOK_VARIABLES:
            raise ValueError(f"unsupported custom webhook variable: {variable_name}")


@dataclass(frozen=True, slots=True)
class RenderedCustomWebhookRequest:
    method: str
    query: dict[str, str]
    body: dict[str, str]

    def to_delivery_payload(self) -> dict[str, object]:
        return {"method": self.method, "query": self.query, "body": self.body}


def _event_value(event: NotificationEventRecord, variable_name: str) -> str:
    if variable_name == "failure_message":
        return event.failure.message if event.failure and event.failure.message else ""
    value = getattr(event, variable_name, None)
    if value is None:
        return ""
    if variable_name in {"occurred_at", "scheduled_at"}:
        return format_datetime_for_message(value) or ""
    return str(value)


def render_custom_template_value(value: str, event: NotificationEventRecord) -> str:
    def replace(match: re.Match[str]) -> str:
        return _event_value(event, match.group(1))

    return _TOKEN_RE.sub(replace, value)


def _render_params(
    params: list[dict[str, str]],
    event: NotificationEventRecord,
) -> dict[str, str]:
    return {
        param["key"]: render_custom_template_value(param.get("value", ""), event)
        for param in params
        if param.get("key")
    }


def render_custom_webhook_request(
    custom_config: dict[str, Any],
    event: NotificationEventRecord,
) -> RenderedCustomWebhookRequest:
    method = str(custom_config.get("method", "POST")).upper()
    query = _render_params(list(custom_config.get("query_params") or []), event)
    body = (
        _render_params(list(custom_config.get("body_params") or []), event)
        if method == "POST"
        else {}
    )
    return RenderedCustomWebhookRequest(method=method, query=query, body=body)


def build_custom_webhook_preview(custom_config: dict[str, Any] | None) -> str:
    if not custom_config:
        return "Custom webhook is not configured."
    method = str(custom_config.get("method", "POST")).upper()
    query_count = len(list(custom_config.get("query_params") or []))
    body_count = len(list(custom_config.get("body_params") or [])) if method == "POST" else 0
    return f"Custom webhook {method}: {query_count} query params, {body_count} body params"
