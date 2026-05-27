from __future__ import annotations

from html import escape as html_escape
from typing import Any, TypedDict

from onestep_control_plane_api.api.notification_helpers import (
    NotificationEventRecord,
    NotificationEventType,
    NotificationProvider,
    build_message_lines,
    event_summary_line,
    is_instance_event_type,
)


class WeComPayload(TypedDict):
    msgtype: str
    markdown: dict[str, str]


_EVENT_HEADER_TEMPLATE: dict[NotificationEventType, str] = {
    "task_started": "blue",
    "task_succeeded": "green",
    "task_failed": "red",
    "task_missed_start": "orange",
    "instance_online": "green",
    "instance_offline": "red",
}


def render_notification_message(event: NotificationEventRecord) -> str:
    return "\n".join(build_message_lines(event))


def _is_absolute_url(url: str | None) -> bool:
    if url is None:
        return False
    return url.startswith(("http://", "https://"))


def _format_wecom_markdown_link(text: str, url: str | None) -> str:
    if url is None:
        return text
    return f"<a href=\"{url}\">{text}</a>"


def _field_line(label: str, value: str | None) -> str | None:
    if value is None:
        return None
    return f"**{label}**：{value}"


_FEISHU_MARKDOWN_ESCAPE_TABLE = str.maketrans(
    {
        "\\": "&#92;",
        "`": "&#96;",
        "*": "&#42;",
        "_": "&#95;",
        "~": "&sim;",
        "[": "&#91;",
        "]": "&#93;",
        "(": "&#40;",
        ")": "&#41;",
    }
)


def _escape_feishu_markdown_text(value: str) -> str:
    # Feishu markdown requires HTML entity escaping for markdown control chars.
    return html_escape(value, quote=True).translate(_FEISHU_MARKDOWN_ESCAPE_TABLE)


def _format_feishu_code_value(value: str) -> str:
    if "`" in value or "\n" in value:
        return _escape_feishu_markdown_text(value)
    return f"`{value}`"


def _build_detail_lines(event: NotificationEventRecord) -> list[str]:
    lines: list[str] = [
        f"**环境**：`{event.service_environment}`",
        f"**Service**：`{event.service_name}`",
    ]
    if event.task_name is not None:
        lines.append(f"**Task**：`{event.task_name}`")
    if event.event_type == "task_started":
        lines.extend(
            line
            for line in (
                _field_line("计划时间", _format_time(event.scheduled_at)),
                _field_line("开始时间", _format_time(event.occurred_at)),
            )
            if line is not None
        )
    elif event.event_type == "task_succeeded":
        lines.extend(
            line
            for line in (
                _field_line("计划时间", _format_time(event.scheduled_at)),
                _field_line("完成时间", _format_time(event.occurred_at)),
                _field_line("耗时", _format_duration(event)),
            )
            if line is not None
        )
        if event.success_summary is not None:
            lines.append(f"**摘要**：{event.success_summary}")
    elif event.event_type == "task_failed":
        lines.extend(
            line
            for line in (
                _field_line("计划时间", _format_time(event.scheduled_at)),
                _field_line("失败时间", _format_time(event.occurred_at)),
                _field_line("耗时", _format_duration(event)),
                _field_line("原因", _format_failure(event)),
            )
            if line is not None
        )
    elif event.event_type == "task_missed_start":
        lines.extend(
            line
            for line in (
                _field_line("计划时间", _format_time(event.scheduled_at)),
                _field_line("宽限时间", _format_grace(event)),
                _field_line("检测时间", _format_time(event.detected_at)),
            )
            if line is not None
        )
        lines.append("**说明**：到达预期时间后未检测到 started 事件")
    elif event.event_type == "instance_online":
        lines.extend(
            line
            for line in (
                _field_line("节点", event.node_name),
                _field_line("实例", event.instance_id),
                _field_line("上线时间", _format_time(event.occurred_at)),
                _field_line("检测时间", _format_time(event.detected_at)),
            )
            if line is not None
        )
    else:
        lines.extend(
            line
            for line in (
                _field_line("节点", event.node_name),
                _field_line("实例", event.instance_id),
                _field_line("最后在线", _format_time(event.last_seen_at)),
                _field_line("离线时间", _format_time(event.occurred_at)),
                _field_line("检测时间", _format_time(event.detected_at)),
            )
            if line is not None
        )

    lines.extend(
        line
        for line in (
            _field_line("尝试次数", str(event.attempts) if event.attempts is not None else None),
            _field_line(
                "实例",
                (
                    event.instance_id
                    if event.instance_id is not None
                    and not is_instance_event_type(event.event_type)
                    else None
                ),
            ),
            _field_line(
                "详情",
                (
                    event.console_url
                    if event.console_url is not None
                    and not _is_absolute_url(event.console_url)
                    else None
                ),
            ),
        )
        if line is not None
    )
    return lines


def _build_success_metric_lines(event: NotificationEventRecord) -> list[str]:
    return [f"• **{metric.label}**：{metric.value}" for metric in event.success_metrics]


def _build_feishu_detail_lines(event: NotificationEventRecord) -> list[str]:
    lines: list[str] = [
        _build_feishu_code_field_line("环境", event.service_environment),
        _build_feishu_code_field_line("Service", event.service_name),
    ]
    if event.task_name is not None:
        lines.append(_build_feishu_code_field_line("Task", event.task_name))
    if event.event_type == "task_started":
        lines.extend(
            line
            for line in (
                _build_feishu_field_line("计划时间", _format_time(event.scheduled_at)),
                _build_feishu_field_line("开始时间", _format_time(event.occurred_at)),
            )
            if line is not None
        )
    elif event.event_type == "task_succeeded":
        lines.extend(
            line
            for line in (
                _build_feishu_field_line("计划时间", _format_time(event.scheduled_at)),
                _build_feishu_field_line("完成时间", _format_time(event.occurred_at)),
                _build_feishu_field_line("耗时", _format_duration(event)),
            )
            if line is not None
        )
        if event.success_summary is not None:
            lines.append(_build_feishu_field_line("摘要", event.success_summary))
    elif event.event_type == "task_failed":
        lines.extend(
            line
            for line in (
                _build_feishu_field_line("计划时间", _format_time(event.scheduled_at)),
                _build_feishu_field_line("失败时间", _format_time(event.occurred_at)),
                _build_feishu_field_line("耗时", _format_duration(event)),
                _build_feishu_field_line("原因", _format_failure(event)),
            )
            if line is not None
        )
    elif event.event_type == "task_missed_start":
        lines.extend(
            line
            for line in (
                _build_feishu_field_line("计划时间", _format_time(event.scheduled_at)),
                _build_feishu_field_line("宽限时间", _format_grace(event)),
                _build_feishu_field_line("检测时间", _format_time(event.detected_at)),
            )
            if line is not None
        )
        lines.append("**说明**：到达预期时间后未检测到 started 事件")
    elif event.event_type == "instance_online":
        lines.extend(
            line
            for line in (
                _build_feishu_code_field_line("节点", event.node_name),
                _build_feishu_code_field_line("实例", event.instance_id),
                _build_feishu_field_line("上线时间", _format_time(event.occurred_at)),
                _build_feishu_field_line("检测时间", _format_time(event.detected_at)),
            )
            if line is not None
        )
    else:
        lines.extend(
            line
            for line in (
                _build_feishu_code_field_line("节点", event.node_name),
                _build_feishu_code_field_line("实例", event.instance_id),
                _build_feishu_field_line("最后在线", _format_time(event.last_seen_at)),
                _build_feishu_field_line("离线时间", _format_time(event.occurred_at)),
                _build_feishu_field_line("检测时间", _format_time(event.detected_at)),
            )
            if line is not None
        )

    lines.extend(
        line
        for line in (
            _build_feishu_field_line(
                "尝试次数",
                str(event.attempts) if event.attempts is not None else None,
            ),
            _build_feishu_field_line(
                "实例",
                event.instance_id
                if event.instance_id is not None and not is_instance_event_type(event.event_type)
                else None,
            ),
            _build_feishu_detail_link_line(event.console_url),
            _build_feishu_code_field_line(
                "详情",
                (
                    event.console_url
                    if event.console_url is not None
                    and not _is_absolute_url(event.console_url)
                    else None
                ),
            ),
        )
        if line is not None
    )
    return lines


def _build_feishu_field_line(label: str, value: str | None) -> str | None:
    if value is None:
        return None
    return f"**{label}**：{_escape_feishu_markdown_text(value)}"


def _build_feishu_code_field_line(label: str, value: str | None) -> str | None:
    if value is None:
        return None
    return f"**{label}**：{_format_feishu_code_value(value)}"


def _build_feishu_detail_link_line(url: str | None) -> str | None:
    if not _is_absolute_url(url):
        return None
    return f"**详情**：[打开详情]({url})"


def _build_feishu_success_metric_lines(event: NotificationEventRecord) -> list[str]:
    return [
        (
            f"• **{_escape_feishu_markdown_text(metric.label)}**："
            f"{_escape_feishu_markdown_text(metric.value)}"
        )
        for metric in event.success_metrics
    ]


def _format_time(value) -> str | None:
    from onestep_control_plane_api.api.notification_helpers import format_datetime_for_message

    return format_datetime_for_message(value)


def _format_duration(event: NotificationEventRecord) -> str | None:
    from onestep_control_plane_api.api.notification_helpers import format_duration_ms

    return format_duration_ms(event.duration_ms)


def _format_failure(event: NotificationEventRecord) -> str | None:
    from onestep_control_plane_api.api.notification_helpers import format_failure_summary

    return format_failure_summary(event.failure)


def _format_grace(event: NotificationEventRecord) -> str | None:
    if event.missed_start_grace_seconds is None:
        return None
    return f"{event.missed_start_grace_seconds}s"


def _build_feishu_card_elements(event: NotificationEventRecord) -> list[dict[str, Any]]:
    elements: list[dict[str, Any]] = [
        {
            "tag": "markdown",
            "content": "\n".join(_build_feishu_detail_lines(event)),
            "text_align": "left",
            "text_size": "normal_v2",
            "margin": "0px 0px 8px 0px",
        }
    ]

    metric_lines = _build_feishu_success_metric_lines(event)
    if metric_lines:
        elements.append(
            {
                "tag": "markdown",
                "content": "**业务指标**\n" + "\n".join(metric_lines),
                "text_align": "left",
                "text_size": "normal_v2",
                "margin": "0px 0px 8px 0px",
            }
        )
    return elements


def build_feishu_payload(event: NotificationEventRecord) -> dict[str, Any]:
    return {
        "msg_type": "interactive",
        "card": {
            "schema": "2.0",
            "config": {
                "update_multi": True,
                "style": {
                    "text_size": {
                        "normal_v2": {
                            "default": "normal",
                            "pc": "normal",
                            "mobile": "heading",
                        }
                    }
                },
            },
            "header": {
                "title": {"tag": "plain_text", "content": event_summary_line(event)},
                "template": _EVENT_HEADER_TEMPLATE[event.event_type],
            },
            "body": {
                "direction": "vertical",
                "padding": "12px 12px 12px 12px",
                "elements": _build_feishu_card_elements(event),
            },
        },
    }


def build_wechat_work_payload(event: NotificationEventRecord) -> WeComPayload:
    detail_lines = _build_detail_lines(event)
    metric_lines = _build_success_metric_lines(event)
    parts = [event_summary_line(event), "", *detail_lines]
    if metric_lines:
        parts.extend(["", "**业务指标**", *metric_lines])
    if _is_absolute_url(event.console_url):
        parts.extend(["", _format_wecom_markdown_link("打开详情", event.console_url)])
    return {
        "msgtype": "markdown",
        "markdown": {"content": "\n".join(parts)},
    }


def build_webhook_payload(
    provider: NotificationProvider, event: NotificationEventRecord
) -> dict[str, Any] | WeComPayload:
    if provider == "feishu":
        return build_feishu_payload(event)
    if provider == "wechat_work":
        return build_wechat_work_payload(event)
    raise ValueError(f"unsupported provider: {provider!r}")
