from __future__ import annotations

from typing import TypedDict

from onestep_control_plane_api.api.notification_helpers import (
    NotificationEventRecord,
    NotificationProvider,
    build_message_lines,
)


class FeishuPayload(TypedDict):
    msg_type: str
    content: dict[str, str]


class WeComPayload(TypedDict):
    msgtype: str
    markdown: dict[str, str]


def render_notification_message(event: NotificationEventRecord) -> str:
    return "\n".join(build_message_lines(event))


def build_feishu_payload(event: NotificationEventRecord) -> FeishuPayload:
    return {
        "msg_type": "text",
        "content": {"text": render_notification_message(event)},
    }


def build_wechat_work_payload(event: NotificationEventRecord) -> WeComPayload:
    return {
        "msgtype": "markdown",
        "markdown": {"content": render_notification_message(event)},
    }


def build_webhook_payload(
    provider: NotificationProvider, event: NotificationEventRecord
) -> FeishuPayload | WeComPayload:
    if provider == "feishu":
        return build_feishu_payload(event)
    if provider == "wechat_work":
        return build_wechat_work_payload(event)
    raise ValueError(f"unsupported provider: {provider!r}")
