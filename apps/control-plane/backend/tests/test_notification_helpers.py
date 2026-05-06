from __future__ import annotations

from datetime import UTC, datetime

import pytest

from onestep_control_plane_api.api.notification_helpers import (
    NotificationEventRecord,
    NotificationFailureInfo,
    NotificationMetricLine,
    build_message_lines,
    event_summary_line,
    format_duration_ms,
    missed_start_dedupe_key,
    normalize_notification_event_type,
    normalize_notification_event_types,
    normalize_service_scope,
    normalize_service_scopes,
    parse_scheduled_at,
    raw_task_event_dedupe_key,
    scheduled_at_from_meta,
    scheduled_at_to_storage_string,
)
from onestep_control_plane_api.api.notification_payloads import (
    build_feishu_payload,
    build_webhook_payload,
    build_wechat_work_payload,
    render_notification_message,
)


def build_event(
    event_type: str = "task_started",
    **overrides,
) -> NotificationEventRecord:
    base = {
        "event_type": event_type,
        "service_name": "billing-worker",
        "service_environment": "prod",
        "task_name": "sync_invoice",
        "occurred_at": datetime(2026, 4, 30, 2, 0, 5, tzinfo=UTC),
        "event_id": "evt-1",
        "scheduled_at": datetime(2026, 4, 30, 2, 0, 0, tzinfo=UTC),
        "duration_ms": 4200,
        "attempts": 1,
        "instance_id": "inst-1",
        "failure": None,
        "success_summary": None,
        "success_metrics": (),
        "console_url": "https://cp.example/services/billing-worker/tasks/sync_invoice",
        "detected_at": None,
        "missed_start_grace_seconds": None,
    }
    base.update(overrides)
    return NotificationEventRecord(**base)


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        ("task_started", "task_started"),
        (" TASK_SUCCEEDED ", "task_succeeded"),
        ("task_failed", "task_failed"),
        ("task_missed_start", "task_missed_start"),
    ],
)
def test_normalize_notification_event_type_accepts_supported_values(raw_value, expected) -> None:
    assert normalize_notification_event_type(raw_value) == expected


def test_normalize_notification_event_type_rejects_unknown_value() -> None:
    with pytest.raises(ValueError, match="unsupported notification event type"):
        normalize_notification_event_type("retried")


def test_normalize_notification_event_types_dedupes_while_preserving_order() -> None:
    assert normalize_notification_event_types(
        [" task_started ", "TASK_FAILED", "task_started", "task_missed_start"]
    ) == ["task_started", "task_failed", "task_missed_start"]


def test_normalize_service_scope_trims_fields() -> None:
    assert normalize_service_scope({"name": " billing-worker ", "environment": " prod "}) == {
        "name": "billing-worker",
        "environment": "prod",
    }


def test_normalize_service_scopes_dedupes_while_preserving_order() -> None:
    assert normalize_service_scopes(
        [
            {"name": "billing-worker", "environment": "prod"},
            {"name": "billing-worker ", "environment": " prod"},
            {"name": "invoice-worker", "environment": "staging"},
        ]
    ) == [
        {"name": "billing-worker", "environment": "prod"},
        {"name": "invoice-worker", "environment": "staging"},
    ]


@pytest.mark.parametrize(
    ("service", "scopes", "expected"),
    [
        (
            {"name": "billing-worker", "environment": "prod"},
            [{"name": "billing-worker", "environment": "prod"}],
            True,
        ),
        (
            {"name": "billing-worker", "environment": "prod"},
            [{"name": "billing-worker", "environment": "staging"}],
            False,
        ),
    ],
)
def test_service_scope_matching(service, scopes, expected) -> None:
    from onestep_control_plane_api.api.notification_helpers import is_service_in_scope

    assert is_service_in_scope(service, scopes) is expected


def test_parse_scheduled_at_accepts_z_suffix_and_normalizes_utc() -> None:
    parsed = parse_scheduled_at("2026-04-30T10:00:00Z")
    assert parsed == datetime(2026, 4, 30, 10, 0, 0, tzinfo=UTC)


def test_parse_scheduled_at_returns_none_for_blank() -> None:
    assert parse_scheduled_at("   ") is None


def test_parse_scheduled_at_rejects_invalid_value() -> None:
    with pytest.raises(ValueError, match="invalid scheduled_at value"):
        parse_scheduled_at("2026/04/30 10:00:00")


def test_scheduled_at_from_meta_reads_iso_string() -> None:
    assert scheduled_at_from_meta({"scheduled_at": "2026-04-30T10:00:00+08:00"}) == datetime(
        2026, 4, 30, 2, 0, 0, tzinfo=UTC
    )


def test_scheduled_at_from_meta_rejects_non_string() -> None:
    with pytest.raises(ValueError, match="scheduled_at value must be a string"):
        scheduled_at_from_meta({"scheduled_at": 123})


def test_scheduled_at_to_storage_string_serializes_as_utc() -> None:
    assert scheduled_at_to_storage_string(datetime(2026, 4, 30, 10, 0, 0, tzinfo=UTC)) == (
        "2026-04-30T10:00:00+00:00"
    )


def test_raw_task_event_dedupe_key() -> None:
    assert raw_task_event_dedupe_key("channel-1", "evt-1") == "channel-1:evt-1"


def test_missed_start_dedupe_key_uses_service_task_and_scheduled_at() -> None:
    assert missed_start_dedupe_key(
        "channel-1",
        service_name="billing-worker",
        service_environment="prod",
        task_name="sync_invoice",
        scheduled_at=datetime(2026, 4, 30, 2, 0, 0, tzinfo=UTC),
    ) == (
        "channel-1:prod:billing-worker:sync_invoice:2026-04-30T02:00:00+00:00:"
        "task_missed_start"
    )


@pytest.mark.parametrize(
    ("duration_ms", "expected"),
    [
        (4200, "4.2s"),
        (500, "0.5s"),
        (1000, "1.0s"),
        (12345, "12.345s"),
    ],
)
def test_format_duration_ms(duration_ms, expected) -> None:
    assert format_duration_ms(duration_ms) == expected


def test_event_summary_line_uses_chinese_label() -> None:
    assert event_summary_line(build_event("task_failed")) == "[任务失败] prod/billing-worker sync_invoice"


def test_build_message_lines_for_started_event() -> None:
    lines = build_message_lines(build_event("task_started"))
    assert lines == [
        "[任务开始] prod/billing-worker sync_invoice",
        "环境: prod",
        "Service: billing-worker",
        "Task: sync_invoice",
        "计划时间: 2026-04-30T02:00:00+00:00",
        "开始时间: 2026-04-30T02:00:05+00:00",
        "尝试次数: 1",
        "实例: inst-1",
        "详情: https://cp.example/services/billing-worker/tasks/sync_invoice",
    ]


def test_build_message_lines_for_succeeded_event() -> None:
    lines = build_message_lines(build_event("task_succeeded"))
    assert "完成时间: 2026-04-30T02:00:05+00:00" in lines
    assert "耗时: 4.2s" in lines


def test_build_message_lines_for_succeeded_event_with_summary_and_metrics() -> None:
    lines = build_message_lines(
        build_event(
            "task_succeeded",
            success_summary="状态同步完成，更新了 12 个设备状态",
            success_metrics=(
                NotificationMetricLine(label="总设备数", value="100"),
                NotificationMetricLine(label="已更新", value="12"),
            ),
        )
    )
    assert lines == [
        "[任务成功] prod/billing-worker sync_invoice",
        "环境: prod",
        "Service: billing-worker",
        "Task: sync_invoice",
        "计划时间: 2026-04-30T02:00:00+00:00",
        "完成时间: 2026-04-30T02:00:05+00:00",
        "耗时: 4.2s",
        "摘要: 状态同步完成，更新了 12 个设备状态",
        "总设备数: 100",
        "已更新: 12",
        "尝试次数: 1",
        "实例: inst-1",
        "详情: https://cp.example/services/billing-worker/tasks/sync_invoice",
    ]


def test_build_message_lines_for_failed_event_includes_failure_summary() -> None:
    lines = build_message_lines(
        build_event(
            "task_failed",
            failure=NotificationFailureInfo(
                kind="runtime_error",
                exception_type="TimeoutError",
                message="upstream mysql timeout",
            ),
        )
    )
    assert "失败时间: 2026-04-30T02:00:05+00:00" in lines
    assert "原因: TimeoutError: runtime_error (upstream mysql timeout)" in lines


def test_build_message_lines_for_missed_start_event() -> None:
    lines = build_message_lines(
        build_event(
            "task_missed_start",
            detected_at=datetime(2026, 4, 30, 2, 5, 0, tzinfo=UTC),
            missed_start_grace_seconds=300,
            duration_ms=None,
            attempts=None,
            instance_id=None,
        )
    )
    assert lines == [
        "[任务未开始] prod/billing-worker sync_invoice",
        "环境: prod",
        "Service: billing-worker",
        "Task: sync_invoice",
        "计划时间: 2026-04-30T02:00:00+00:00",
        "宽限时间: 300s",
        "检测时间: 2026-04-30T02:05:00+00:00",
        "说明: 到达预期时间后未检测到 started 事件",
        "详情: https://cp.example/services/billing-worker/tasks/sync_invoice",
    ]


def test_render_notification_message_joins_lines() -> None:
    message = render_notification_message(build_event("task_started"))
    assert message.splitlines()[0] == "[任务开始] prod/billing-worker sync_invoice"


def test_build_feishu_payload_uses_card_message() -> None:
    payload = build_feishu_payload(build_event("task_started"))
    assert payload["msg_type"] == "interactive"
    assert payload["card"]["header"]["title"]["content"] == "[任务开始] prod/billing-worker sync_invoice"
    assert payload["card"]["header"]["template"] == "blue"
    assert len(payload["card"]["elements"]) == 1
    assert "环境: prod" in payload["card"]["elements"][0]["text"]["content"]


def test_build_feishu_payload_uses_red_header_for_failed() -> None:
    payload = build_feishu_payload(build_event("task_failed"))
    assert payload["card"]["header"]["template"] == "red"
    assert "任务失败" in payload["card"]["header"]["title"]["content"]


def test_build_wechat_work_payload_uses_markdown_message() -> None:
    payload = build_wechat_work_payload(build_event("task_failed"))
    assert payload["msgtype"] == "markdown"
    assert payload["markdown"]["content"].startswith("[任务失败]")


def test_success_metrics_are_rendered_into_both_webhook_payload_formats() -> None:
    event = build_event(
        "task_succeeded",
        success_summary="状态同步完成，更新了 12 个设备状态",
        success_metrics=(
            NotificationMetricLine(label="总设备数", value="100"),
            NotificationMetricLine(label="已检查", value="100"),
        ),
    )

    feishu_payload = build_feishu_payload(event)
    assert "摘要: 状态同步完成，更新了 12 个设备状态" in feishu_payload["card"]["elements"][0]["text"][
        "content"
    ]
    assert "总设备数: 100" in feishu_payload["card"]["elements"][0]["text"]["content"]
    assert "已检查: 100" in feishu_payload["card"]["elements"][0]["text"]["content"]

    wecom_payload = build_wechat_work_payload(event)
    assert "摘要: 状态同步完成，更新了 12 个设备状态" in wecom_payload["markdown"]["content"]
    assert "总设备数: 100" in wecom_payload["markdown"]["content"]
    assert "已检查: 100" in wecom_payload["markdown"]["content"]


@pytest.mark.parametrize("provider", ["feishu", "wechat_work"])
def test_build_webhook_payload_dispatches_to_provider(provider) -> None:
    payload = build_webhook_payload(provider, build_event("task_succeeded"))
    assert isinstance(payload, dict)


def test_build_webhook_payload_rejects_unknown_provider() -> None:
    with pytest.raises(ValueError, match="unsupported provider"):
        build_webhook_payload("slack", build_event("task_started"))  # type: ignore[arg-type]
