from __future__ import annotations

from typing import Any, TypedDict

from onestep_control_plane_api.api.notification_helpers import (
    NotificationEventRecord,
    NotificationEventType,
    NotificationProvider,
    build_message_lines,
    event_summary_line,
)


class WeComPayload(TypedDict):
    msgtype: str
    markdown: dict[str, str]


_EVENT_HEADER_TEMPLATE: dict[NotificationEventType, str] = {
    "task_started": "blue",
    "task_succeeded": "green",
    "task_failed": "red",
    "task_missed_start": "orange",
}


def render_notification_message(event: NotificationEventRecord) -> str:
    return "\n".join(build_message_lines(event))


def _build_feishu_card_elements(event: NotificationEventRecord) -> list[dict[str, Any]]:
    lines = build_message_lines(event)
    # Skip the first line (summary) — it goes into the header
    detail_lines = lines[1:]
    body = "\n".join(detail_lines)
    return [
        {
            "tag": "div",
            "text": {"tag": "lark_md", "content": body},
        },
    ]


def build_feishu_payload(event: NotificationEventRecord) -> dict[str, Any]:
    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": event_summary_line(event)},
                "template": _EVENT_HEADER_TEMPLATE[event.event_type],
            },
            "elements": _build_feishu_card_elements(event),
        },
    }


def build_wechat_work_payload(event: NotificationEventRecord) -> WeComPayload:
    return {
        "msgtype": "markdown",
        "markdown": {"content": render_notification_message(event)},
    }


def build_webhook_payload(
    provider: NotificationProvider, event: NotificationEventRecord
) -> dict[str, Any] | WeComPayload:
    if provider == "feishu":
        return build_feishu_payload(event)
    if provider == "wechat_work":
        return build_wechat_work_payload(event)
    raise ValueError(f"unsupported provider: {provider!r}")
