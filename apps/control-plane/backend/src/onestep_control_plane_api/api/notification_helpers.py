from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final, Literal, TypedDict

from onestep_control_plane_api.core.settings import settings

NotificationEventType = Literal[
    "task_started",
    "task_succeeded",
    "task_failed",
    "task_missed_start",
]
NotificationProvider = Literal["feishu", "wechat_work"]

NOTIFICATION_EVENT_TYPES: Final[tuple[NotificationEventType, ...]] = (
    "task_started",
    "task_succeeded",
    "task_failed",
    "task_missed_start",
)
NOTIFICATION_EVENT_TYPE_SET: Final[frozenset[str]] = frozenset(NOTIFICATION_EVENT_TYPES)


class ServiceScope(TypedDict):
    name: str
    environment: str


@dataclass(frozen=True, slots=True)
class NotificationServiceRef:
    name: str
    environment: str


@dataclass(frozen=True, slots=True)
class NotificationFailureInfo:
    kind: str | None = None
    exception_type: str | None = None
    message: str | None = None


@dataclass(frozen=True, slots=True)
class NotificationMetricLine:
    label: str
    value: str


@dataclass(frozen=True, slots=True)
class NotificationEventRecord:
    event_type: NotificationEventType
    service_name: str
    service_environment: str
    task_name: str
    occurred_at: datetime
    event_id: str | None = None
    scheduled_at: datetime | None = None
    duration_ms: int | None = None
    attempts: int | None = None
    instance_id: str | None = None
    failure: NotificationFailureInfo | None = None
    success_summary: str | None = None
    success_metrics: tuple[NotificationMetricLine, ...] = ()
    console_url: str | None = None
    detected_at: datetime | None = None
    missed_start_grace_seconds: int | None = None


def normalize_notification_event_type(raw_value: str) -> NotificationEventType:
    normalized = raw_value.strip().lower()
    if normalized not in NOTIFICATION_EVENT_TYPE_SET:
        raise ValueError(f"unsupported notification event type: {raw_value!r}")
    return normalized  # type: ignore[return-value]


def normalize_notification_event_types(raw_values: list[str]) -> list[NotificationEventType]:
    normalized: list[NotificationEventType] = []
    seen: set[str] = set()
    for raw_value in raw_values:
        event_type = normalize_notification_event_type(raw_value)
        if event_type in seen:
            continue
        seen.add(event_type)
        normalized.append(event_type)
    return normalized


def normalize_service_scope(
    scope: ServiceScope | NotificationServiceRef | dict[str, Any],
) -> ServiceScope:
    name = _normalize_required_string(_get_scope_value(scope, "name"), field_name="name")
    environment = _normalize_required_string(
        _get_scope_value(scope, "environment"), field_name="environment"
    )
    return {"name": name, "environment": environment}


def normalize_service_scopes(
    scopes: list[ServiceScope | NotificationServiceRef | dict[str, Any]],
) -> list[ServiceScope]:
    normalized: list[ServiceScope] = []
    seen: set[tuple[str, str]] = set()
    for scope in scopes:
        normalized_scope = normalize_service_scope(scope)
        key = (normalized_scope["name"], normalized_scope["environment"])
        if key in seen:
            continue
        seen.add(key)
        normalized.append(normalized_scope)
    return normalized


def is_service_in_scope(
    service: ServiceScope | NotificationServiceRef | dict[str, Any],
    scopes: list[ServiceScope | NotificationServiceRef | dict[str, Any]],
) -> bool:
    normalized_service = normalize_service_scope(service)
    for scope in scopes:
        normalized_scope = normalize_service_scope(scope)
        if (
            normalized_scope["name"] == normalized_service["name"]
            and normalized_scope["environment"] == normalized_service["environment"]
        ):
            return True
    return False


def as_utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def parse_scheduled_at(raw_value: str | None) -> datetime | None:
    if raw_value is None:
        return None
    normalized = raw_value.strip()
    if not normalized:
        return None
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"invalid scheduled_at value: {raw_value!r}") from exc
    return as_utc_datetime(parsed)


def scheduled_at_to_storage_string(value: datetime | None) -> str | None:
    if value is None:
        return None
    return as_utc_datetime(value).isoformat()


def scheduled_at_from_meta(meta: dict[str, Any] | None) -> datetime | None:
    if not meta:
        return None
    raw_value = meta.get("scheduled_at")
    if raw_value is None:
        return None
    if not isinstance(raw_value, str):
        raise ValueError("scheduled_at value must be a string")
    return parse_scheduled_at(raw_value)


def raw_task_event_dedupe_key(channel_id: str, event_id: str) -> str:
    return f"{channel_id}:{event_id}"


def missed_start_dedupe_key(
    channel_id: str,
    *,
    service_name: str,
    service_environment: str,
    task_name: str,
    scheduled_at: datetime,
) -> str:
    normalized_name = _normalize_required_string(service_name, field_name="service_name")
    normalized_environment = _normalize_required_string(
        service_environment, field_name="service_environment"
    )
    normalized_task_name = _normalize_required_string(task_name, field_name="task_name")
    scheduled_at_key = scheduled_at_to_storage_string(scheduled_at)
    assert scheduled_at_key is not None
    return (
        f"{channel_id}:{normalized_environment}:{normalized_name}:{normalized_task_name}:"
        f"{scheduled_at_key}:task_missed_start"
    )


def event_display_label(event_type: NotificationEventType) -> str:
    return {
        "task_started": "任务开始",
        "task_succeeded": "任务成功",
        "task_failed": "任务失败",
        "task_missed_start": "任务未开始",
    }[event_type]


def event_summary_line(event: NotificationEventRecord) -> str:
    return (
        f"[{event_display_label(event.event_type)}] "
        f"{event.service_environment}/{event.service_name} {event.task_name}"
    )


def format_datetime_for_message(value: datetime | None) -> str | None:
    if value is None:
        return None
    return as_utc_datetime(value).astimezone(settings.effective_api_response_timezone).isoformat()


def format_duration_ms(duration_ms: int | None) -> str | None:
    if duration_ms is None:
        return None
    seconds = duration_ms / 1000
    formatted = f"{seconds:.3f}".rstrip("0").rstrip(".")
    if "." not in formatted:
        formatted = f"{formatted}.0"
    return f"{formatted}s"


def format_failure_summary(failure: NotificationFailureInfo | None) -> str | None:
    if failure is None:
        return None
    parts = [part for part in (failure.exception_type, failure.kind, failure.message) if part]
    if not parts:
        return None
    return ": ".join(parts[:2]) if len(parts) <= 2 else f"{parts[0]}: {parts[1]} ({parts[2]})"


def build_message_lines(event: NotificationEventRecord) -> list[str]:
    lines = [event_summary_line(event)]
    lines.append(f"环境: {event.service_environment}")
    lines.append(f"Service: {event.service_name}")
    lines.append(f"Task: {event.task_name}")

    scheduled_at = format_datetime_for_message(event.scheduled_at)
    occurred_at = format_datetime_for_message(event.occurred_at)
    detected_at = format_datetime_for_message(event.detected_at)

    if event.event_type == "task_started":
        if scheduled_at is not None:
            lines.append(f"计划时间: {scheduled_at}")
        lines.append(f"开始时间: {occurred_at}")
    elif event.event_type == "task_succeeded":
        if scheduled_at is not None:
            lines.append(f"计划时间: {scheduled_at}")
        lines.append(f"完成时间: {occurred_at}")
        duration = format_duration_ms(event.duration_ms)
        if duration is not None:
            lines.append(f"耗时: {duration}")
        if event.success_summary is not None:
            lines.append(f"摘要: {event.success_summary}")
        for metric in event.success_metrics:
            lines.append(f"{metric.label}: {metric.value}")
    elif event.event_type == "task_failed":
        if scheduled_at is not None:
            lines.append(f"计划时间: {scheduled_at}")
        lines.append(f"失败时间: {occurred_at}")
        duration = format_duration_ms(event.duration_ms)
        if duration is not None:
            lines.append(f"耗时: {duration}")
        failure_summary = format_failure_summary(event.failure)
        if failure_summary is not None:
            lines.append(f"原因: {failure_summary}")
    else:
        assert event.event_type == "task_missed_start"
        if scheduled_at is not None:
            lines.append(f"计划时间: {scheduled_at}")
        if event.missed_start_grace_seconds is not None:
            lines.append(f"宽限时间: {event.missed_start_grace_seconds}s")
        if detected_at is not None:
            lines.append(f"检测时间: {detected_at}")
        lines.append("说明: 到达预期时间后未检测到 started 事件")

    if event.attempts is not None:
        lines.append(f"尝试次数: {event.attempts}")
    if event.instance_id is not None:
        lines.append(f"实例: {event.instance_id}")
    if event.console_url is not None:
        lines.append(f"详情: {event.console_url}")
    return lines


def _normalize_required_string(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a non-empty string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must be a non-empty string")
    return normalized


def _get_scope_value(
    scope: ServiceScope | NotificationServiceRef | dict[str, Any],
    field_name: str,
) -> Any:
    if isinstance(scope, dict):
        return scope.get(field_name)
    return getattr(scope, field_name, None)
