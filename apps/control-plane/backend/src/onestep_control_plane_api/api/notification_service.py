from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from onestep_control_plane_api.api.common import utcnow
from onestep_control_plane_api.api.notification_helpers import (
    NotificationEventRecord,
    NotificationFailureInfo,
    NotificationMetricLine,
    instance_connectivity_dedupe_key,
    is_service_in_scope,
    missed_start_dedupe_key,
    raw_task_event_dedupe_key,
    scheduled_at_from_meta,
)
from onestep_control_plane_api.api.notification_payloads import build_webhook_payload
from onestep_control_plane_api.api.query_support import (
    get_instance_connectivity,
    online_cutoff,
)
from onestep_control_plane_api.api.schemas import (
    NotificationChannelCreateRequest,
    NotificationChannelEnabledPatchRequest,
    NotificationChannelSummary,
    NotificationChannelUpdateRequest,
    NotificationServiceListResponse,
    NotificationServiceOption,
    NotificationTestRequest,
    NotificationTestResponse,
)
from onestep_control_plane_api.core.settings import settings
from onestep_control_plane_api.db.models import (
    Instance,
    NotificationChannel,
    NotificationDelivery,
    NotificationInstanceState,
    Service,
    TaskDefinition,
    TaskEvent,
)

DEFAULT_WEBHOOK_TIMEOUT_S = 5.0
DEFAULT_MISSED_START_SCAN_LOOKBACK_LIMIT = 32
MISSED_START_SCAN_MAX_WINDOW = timedelta(hours=24)
INSTANCE_CONNECTIVITY_EVENT_TYPES = frozenset({"instance_online", "instance_offline"})


def _normalize_success_summary(raw_summary: Any) -> str | None:
    if not isinstance(raw_summary, str):
        return None
    normalized = raw_summary.strip()
    return normalized or None


def _normalize_success_metric_line(raw_metric: Any) -> NotificationMetricLine | None:
    if not isinstance(raw_metric, dict):
        return None

    raw_label = raw_metric.get("label")
    if not isinstance(raw_label, str):
        return None
    label = raw_label.strip()
    if not label:
        return None

    raw_value = raw_metric.get("value")
    if raw_value is None or isinstance(raw_value, dict | list):
        return None

    value = raw_value.strip() if isinstance(raw_value, str) else str(raw_value)
    if not value:
        return None
    return NotificationMetricLine(label=label, value=value)


def _parse_success_notification_payload(
    meta_json: dict[str, Any] | None,
) -> tuple[str | None, tuple[NotificationMetricLine, ...]]:
    if not meta_json:
        return None, ()

    raw_notification = meta_json.get("notification")
    if not isinstance(raw_notification, dict):
        return None, ()

    summary = _normalize_success_summary(raw_notification.get("summary"))

    raw_metrics = raw_notification.get("metrics")
    if not isinstance(raw_metrics, list):
        return summary, ()

    metrics = tuple(
        metric
        for raw_metric in raw_metrics
        if (metric := _normalize_success_metric_line(raw_metric)) is not None
    )
    return summary, metrics


def _mask_webhook_url(webhook_url: str) -> str:
    normalized = webhook_url.strip()
    if not normalized:
        return ""

    parsed = urlsplit(normalized)
    host = parsed.netloc or parsed.path.split("/", 1)[0]
    suffix = normalized[-4:] if len(normalized) > 4 else normalized
    if host:
        return f"{parsed.scheme or 'https'}://{host}/...{suffix}"
    if len(normalized) <= 12:
        return "*" * len(normalized)
    return f"{normalized[:8]}...{suffix}"


def _build_channel_summary(channel: NotificationChannel) -> NotificationChannelSummary:
    return NotificationChannelSummary(
        id=channel.id,
        name=channel.name,
        provider=channel.provider,
        webhook_url_masked=_mask_webhook_url(channel.webhook_url),
        enabled=channel.enabled,
        service_scopes=channel.service_scopes_json,
        event_types=channel.event_types_json,
        missed_start_grace_seconds=channel.missed_start_grace_seconds,
        created_at=channel.created_at,
        updated_at=channel.updated_at,
    )


def _ensure_channel_name_available(error: IntegrityError) -> None:
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="notification channel name already exists",
    ) from error


def _get_channel_or_404(db: Session, channel_id) -> NotificationChannel:
    channel = db.get(NotificationChannel, channel_id)
    if channel is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="notification channel not found",
        )
    return channel


def _validate_merged_missed_start_settings(
    event_types: list[str],
    missed_start_grace_seconds: int,
) -> None:
    if "task_missed_start" not in event_types and missed_start_grace_seconds != 300:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "missed_start_grace_seconds can only be customized when "
                "event_types includes task_missed_start"
            ),
        )


def _scope_to_json(scope: Any) -> dict[str, Any]:
    if hasattr(scope, "model_dump"):
        return scope.model_dump()
    if isinstance(scope, dict):
        return dict(scope)
    raise TypeError("unsupported notification scope value")


def _build_runtime_notification_event(event: TaskEvent) -> NotificationEventRecord | None:
    event_type_map = {
        "started": "task_started",
        "succeeded": "task_succeeded",
        "failed": "task_failed",
    }
    event_type = event_type_map.get(event.kind)
    if event_type is None:
        return None

    success_summary: str | None = None
    success_metrics: tuple[NotificationMetricLine, ...] = ()
    if event.kind == "succeeded":
        success_summary, success_metrics = _parse_success_notification_payload(event.meta_json)

    return NotificationEventRecord(
        event_type=event_type,
        service_name=event.service.name,
        service_environment=event.service.environment,
        task_name=event.task_name,
        occurred_at=event.occurred_at,
        event_id=event.event_id,
        scheduled_at=scheduled_at_from_meta(event.meta_json),
        duration_ms=event.duration_ms,
        attempts=event.attempts,
        instance_id=str(event.instance_id),
        failure=NotificationFailureInfo(
            kind=event.failure_kind,
            exception_type=event.exception_type,
            message=event.message,
        )
        if event.kind == "failed"
        else None,
        success_summary=success_summary,
        success_metrics=success_metrics,
        console_url=settings.build_console_url(
            f"/services/{event.service.name}/tasks/{event.task_name}"
            f"?environment={event.service.environment}"
        ),
    )


def _instance_transition_time(
    instance: Instance,
    *,
    connectivity: str,
    now: datetime,
) -> datetime:
    if connectivity == "online":
        return instance.last_seen_at or now
    if instance.last_seen_at is None:
        return now
    return min(
        now,
        instance.last_seen_at + timedelta(seconds=settings.instance_offline_after_s),
    )


def _build_instance_connectivity_notification_event(
    instance: Instance,
    *,
    service: Service,
    connectivity: str,
    now: datetime,
) -> NotificationEventRecord:
    event_type = "instance_online" if connectivity == "online" else "instance_offline"
    transition_at = _instance_transition_time(instance, connectivity=connectivity, now=now)
    return NotificationEventRecord(
        event_type=event_type,
        service_name=service.name,
        service_environment=service.environment,
        task_name=None,
        occurred_at=transition_at,
        instance_id=str(instance.instance_id),
        node_name=instance.node_name,
        last_seen_at=instance.last_seen_at,
        detected_at=now,
        console_url=settings.build_console_url(
            f"/services/{service.name}/instances/{instance.instance_id}"
            f"?environment={service.environment}"
        ),
    )


def _service_matches_channel(
    channel: NotificationChannel,
    *,
    service_name: str,
    service_environment: str,
) -> bool:
    if not channel.service_scopes_json:
        return True
    return is_service_in_scope(
        {"name": service_name, "environment": service_environment},
        channel.service_scopes_json,
    )


def _matching_channels_for_service(
    db: Session,
    *,
    service_name: str,
    service_environment: str,
    event_type: str,
) -> list[NotificationChannel]:
    channels = db.scalars(
        select(NotificationChannel).where(NotificationChannel.enabled.is_(True))
    ).all()
    return [
        channel
        for channel in channels
        if event_type in channel.event_types_json
        and _service_matches_channel(
            channel,
            service_name=service_name,
            service_environment=service_environment,
        )
    ]


def _persist_pending_delivery(
    db: Session,
    *,
    channel: NotificationChannel,
    notification_event: NotificationEventRecord,
    dedupe_key: str,
    task_event_id: str | None,
    scheduled_at: datetime | None,
) -> NotificationDelivery | None:
    savepoint = db.begin_nested()
    delivery = NotificationDelivery(
        channel=channel,
        dedupe_key=dedupe_key,
        event_type=notification_event.event_type,
        service_name=notification_event.service_name,
        service_environment=notification_event.service_environment,
        task_name=notification_event.task_name,
        task_event_id=task_event_id,
        scheduled_at=scheduled_at,
        status="pending",
        request_payload_json=build_webhook_payload(channel.provider, notification_event),
    )
    db.add(delivery)
    try:
        db.flush()
    except IntegrityError:
        savepoint.rollback()
        return None
    savepoint.commit()
    db.refresh(delivery)
    return delivery


def _post_webhook(
    delivery: NotificationDelivery,
    *,
    webhook_url: str,
    timeout_s: float = DEFAULT_WEBHOOK_TIMEOUT_S,
) -> None:
    try:
        with httpx.Client(timeout=timeout_s) as client:
            response = client.post(webhook_url, json=delivery.request_payload_json)
        delivery.response_status_code = response.status_code
        delivery.response_body = response.text[:4000] if response.text else None
        delivery.status = "succeeded" if response.is_success else "failed"
        if not response.is_success:
            delivery.error_message = f"webhook responded with status {response.status_code}"
    except Exception as exc:
        delivery.status = "failed"
        delivery.error_message = str(exc)
    finally:
        delivery.sent_at = utcnow()


def _dispatch_delivery(
    db: Session,
    *,
    delivery: NotificationDelivery,
    webhook_url: str,
    timeout_s: float = DEFAULT_WEBHOOK_TIMEOUT_S,
) -> None:
    _post_webhook(delivery, webhook_url=webhook_url, timeout_s=timeout_s)
    db.commit()


def _normalize_scan_now(now: datetime | None) -> datetime:
    if now is None:
        return utcnow()
    if now.tzinfo is None:
        return now.replace(tzinfo=UTC)
    return now.astimezone(UTC)


def _online_service_started_at_by_id(
    db: Session,
    *,
    now: datetime,
    min_last_seen_at: datetime | None = None,
) -> dict[object, datetime]:
    online_cutoff = now - timedelta(seconds=settings.instance_offline_after_s)
    if min_last_seen_at is not None:
        online_cutoff = max(online_cutoff, _normalize_scan_now(min_last_seen_at))
    rows = db.execute(
        select(
            Instance.service_id,
            func.min(
                func.coalesce(Instance.started_at, Instance.created_at)
            ).label("online_started_at"),
        )
        .where(
            Instance.last_seen_at.is_not(None),
            Instance.last_seen_at >= online_cutoff,
        )
        .group_by(Instance.service_id)
    ).all()
    return {
        row.service_id: row.online_started_at
        for row in rows
        if row.online_started_at is not None
    }


def _parse_task_timezone(raw_value: Any) -> str | None:
    if isinstance(raw_value, str):
        normalized = raw_value.strip()
        return normalized or None
    return None


def _resolve_task_timezone(raw_value: Any):
    timezone_name = _parse_task_timezone(raw_value)
    if timezone_name is None:
        return settings.effective_api_response_timezone
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        return settings.effective_api_response_timezone


def _derive_interval_anchor(
    *,
    now: datetime,
    source_config: dict[str, Any],
    service: Service,
    interval_seconds: int,
    online_started_at: datetime | None = None,
) -> datetime | None:
    immediate = bool(source_config.get("immediate", False))
    current_time = _normalize_scan_now(now)
    candidates: list[datetime] = []
    for candidate in (service.latest_sync_at, online_started_at, service.created_at):
        if candidate is None:
            continue
        normalized_candidate = _normalize_scan_now(candidate)
        if normalized_candidate <= current_time:
            candidates.append(normalized_candidate)
    candidate = max(candidates) if candidates else None
    if candidate is None:
        return None
    if immediate:
        return candidate
    return candidate + timedelta(seconds=interval_seconds)


def _interval_seconds_from_source_config(source_config: dict[str, Any] | None) -> int | None:
    if not isinstance(source_config, dict):
        return None
    seconds_value = source_config.get("seconds")
    if isinstance(seconds_value, bool) or not isinstance(seconds_value, (int, float)):
        return None
    interval_seconds = int(seconds_value)
    if interval_seconds <= 0:
        return None
    return interval_seconds


def _iter_expected_interval_slots(
    *,
    now: datetime,
    grace_seconds: int,
    source_config: dict[str, Any] | None,
) -> list[datetime]:
    if not isinstance(source_config, dict):
        return []
    seconds_value = source_config.get("seconds")
    if isinstance(seconds_value, bool) or not isinstance(seconds_value, (int, float)):
        return []
    interval_seconds = int(seconds_value)
    if interval_seconds <= 0:
        return []

    return []


def _iter_expected_interval_slots_for_task(
    *,
    now: datetime,
    grace_seconds: int,
    task_definition: TaskDefinition,
    online_started_at: datetime | None = None,
) -> list[datetime]:
    source_config = task_definition.source_config_json
    interval_seconds = _interval_seconds_from_source_config(source_config)
    if interval_seconds is None:
        return []
    service = task_definition.service
    anchor = _derive_interval_anchor(
        now=now,
        source_config=source_config,
        service=service,
        interval_seconds=interval_seconds,
        online_started_at=online_started_at,
    )
    if anchor is None:
        return []
    timezone = _resolve_task_timezone(source_config.get("timezone"))
    anchor_local = anchor.astimezone(timezone)
    due_cutoff = now.astimezone(timezone) - timedelta(seconds=grace_seconds)
    elapsed_seconds = int((due_cutoff - anchor_local).total_seconds())
    if elapsed_seconds < 0:
        return []
    slot_count = min(
        DEFAULT_MISSED_START_SCAN_LOOKBACK_LIMIT,
        (elapsed_seconds // interval_seconds) + 1,
    )
    scheduled_slots: list[datetime] = []
    for offset in range(slot_count):
        slot_index = (elapsed_seconds // interval_seconds) - offset
        if slot_index < 0:
            break
        scheduled_local = anchor_local + timedelta(seconds=slot_index * interval_seconds)
        scheduled_utc = scheduled_local.astimezone(UTC)
        if now - scheduled_utc > MISSED_START_SCAN_MAX_WINDOW:
            break
        scheduled_slots.append(scheduled_utc)
    return scheduled_slots


def _cron_field_matches(field: str, value: int, *, minimum: int, maximum: int) -> bool:
    normalized = field.strip().lower()
    if normalized == "*":
        return True
    for raw_part in normalized.split(","):
        part = raw_part.strip()
        if not part:
            continue
        step = 1
        base = part
        if "/" in part:
            base, step_raw = part.split("/", 1)
            try:
                step = int(step_raw)
            except ValueError:
                return False
            if step <= 0:
                return False
        if base == "*":
            start = minimum
            end = maximum
        elif "-" in base:
            start_raw, end_raw = base.split("-", 1)
            try:
                start = int(start_raw)
                end = int(end_raw)
            except ValueError:
                return False
        else:
            try:
                return int(base) == value
            except ValueError:
                return False
        if start <= value <= end and (value - start) % step == 0:
            return True
    return False


def _cron_matches(dt: datetime, expression: str) -> bool:
    fields = expression.strip().split()
    if len(fields) != 5:
        return False
    minute, hour, day_of_month, month, day_of_week = fields
    cron_weekday = (dt.weekday() + 1) % 7
    dom_any = day_of_month.strip() == "*"
    dow_any = day_of_week.strip() == "*"
    dom_match = _cron_field_matches(day_of_month, dt.day, minimum=1, maximum=31)
    dow_match = _cron_field_matches(day_of_week, cron_weekday, minimum=0, maximum=6)
    if dom_any and dow_any:
        day_match = True
    elif dom_any:
        day_match = dow_match
    elif dow_any:
        day_match = dom_match
    else:
        day_match = dom_match or dow_match
    return (
        _cron_field_matches(minute, dt.minute, minimum=0, maximum=59)
        and _cron_field_matches(hour, dt.hour, minimum=0, maximum=23)
        and _cron_field_matches(month, dt.month, minimum=1, maximum=12)
        and day_match
    )


def _iter_expected_cron_slots(
    *,
    now: datetime,
    grace_seconds: int,
    source_config: dict[str, Any] | None,
) -> list[datetime]:
    if not isinstance(source_config, dict):
        return []
    expression = source_config.get("expression")
    if not isinstance(expression, str) or not expression.strip():
        return []

    timezone = _resolve_task_timezone(source_config.get("timezone"))

    due_cutoff = now.astimezone(timezone) - timedelta(seconds=grace_seconds)
    candidate = due_cutoff.replace(second=0, microsecond=0)
    slots: list[datetime] = []
    scanned = 0
    while scanned < DEFAULT_MISSED_START_SCAN_LOOKBACK_LIMIT:
        if now - candidate.astimezone(UTC) > MISSED_START_SCAN_MAX_WINDOW:
            break
        if _cron_matches(candidate, expression):
            slots.append(candidate.astimezone(UTC))
            scanned += 1
        candidate -= timedelta(minutes=1)
    return slots


def _iter_expected_slots_for_task(
    *,
    now: datetime,
    task_definition: TaskDefinition,
    grace_seconds: int,
    online_started_at: datetime | None = None,
) -> list[datetime]:
    if task_definition.source_kind == "interval":
        return _iter_expected_interval_slots_for_task(
            now=now,
            grace_seconds=grace_seconds,
            task_definition=task_definition,
            online_started_at=online_started_at,
        )
    if task_definition.source_kind == "cron":
        return _iter_expected_cron_slots(
            now=now,
            grace_seconds=grace_seconds,
            source_config=task_definition.source_config_json,
        )
    return []


def _task_started_for_scheduled_slot(
    db: Session,
    *,
    service_id,
    task_name: str,
    scheduled_at: datetime,
    interval_seconds: int | None = None,
) -> bool:
    started_events = db.scalars(
        select(TaskEvent)
        .where(
            TaskEvent.service_id == service_id,
            TaskEvent.task_name == task_name,
            TaskEvent.kind == "started",
            TaskEvent.occurred_at >= scheduled_at - timedelta(hours=1),
            TaskEvent.occurred_at <= scheduled_at + timedelta(days=1),
        )
        .order_by(TaskEvent.occurred_at.desc())
    ).all()
    slot_match_tolerance = _scheduled_slot_match_tolerance(interval_seconds)
    for started_event in started_events:
        try:
            started_scheduled_at = scheduled_at_from_meta(started_event.meta_json)
        except ValueError:
            started_scheduled_at = None
        if started_scheduled_at == scheduled_at:
            return True
        if (
            started_scheduled_at is not None
            and slot_match_tolerance is not None
            and abs(started_scheduled_at - scheduled_at) <= slot_match_tolerance
        ):
            return True
        if (
            started_scheduled_at is None
            and slot_match_tolerance is not None
            and abs(started_event.occurred_at - scheduled_at) <= slot_match_tolerance
        ):
            return True
    return False


def _scheduled_slot_match_tolerance(interval_seconds: int | None) -> timedelta | None:
    if interval_seconds is None:
        return None
    return timedelta(seconds=max(1, min(60, (interval_seconds - 1) // 2)))


def list_notification_channels(db: Session) -> list[NotificationChannelSummary]:
    channels = db.scalars(
        select(NotificationChannel).order_by(
            NotificationChannel.created_at,
            NotificationChannel.name,
        )
    ).all()
    return [_build_channel_summary(channel) for channel in channels]


def create_notification_channel(
    db: Session,
    payload: NotificationChannelCreateRequest,
) -> NotificationChannelSummary:
    channel = NotificationChannel(
        name=payload.name,
        provider=payload.provider,
        webhook_url=payload.webhook_url,
        enabled=payload.enabled,
        service_scopes_json=[_scope_to_json(scope) for scope in payload.service_scopes],
        event_types_json=list(payload.event_types),
        missed_start_grace_seconds=payload.missed_start_grace_seconds,
    )
    db.add(channel)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        _ensure_channel_name_available(error)
    db.refresh(channel)
    return _build_channel_summary(channel)


def update_notification_channel(
    db: Session,
    channel_id,
    payload: NotificationChannelUpdateRequest,
) -> NotificationChannelSummary:
    channel = _get_channel_or_404(db, channel_id)
    update_data = payload.model_dump(exclude_unset=True)

    merged_event_types = (
        list(update_data["event_types"])
        if "event_types" in update_data
        else list(channel.event_types_json)
    )
    merged_missed_start_grace_seconds = (
        update_data["missed_start_grace_seconds"]
        if "missed_start_grace_seconds" in update_data
        else channel.missed_start_grace_seconds
    )
    _validate_merged_missed_start_settings(
        merged_event_types,
        merged_missed_start_grace_seconds,
    )

    if "name" in update_data:
        channel.name = update_data["name"]
    if "provider" in update_data:
        channel.provider = update_data["provider"]
    if "webhook_url" in update_data:
        channel.webhook_url = update_data["webhook_url"]
    if "enabled" in update_data:
        channel.enabled = update_data["enabled"]
    if "service_scopes" in update_data:
        channel.service_scopes_json = [
            _scope_to_json(scope) for scope in update_data["service_scopes"]
        ]
    if "event_types" in update_data:
        channel.event_types_json = list(update_data["event_types"])
    if "missed_start_grace_seconds" in update_data:
        channel.missed_start_grace_seconds = update_data["missed_start_grace_seconds"]

    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        _ensure_channel_name_available(error)
    db.refresh(channel)
    return _build_channel_summary(channel)


def update_notification_channel_enabled(
    db: Session,
    channel_id,
    payload: NotificationChannelEnabledPatchRequest,
) -> NotificationChannelSummary:
    channel = _get_channel_or_404(db, channel_id)
    channel.enabled = payload.enabled
    db.commit()
    db.refresh(channel)
    return _build_channel_summary(channel)


def delete_notification_channel(db: Session, channel_id) -> None:
    channel = _get_channel_or_404(db, channel_id)
    db.delete(channel)
    db.commit()


def build_notification_test_response(
    db: Session,
    channel_id,
    payload: NotificationTestRequest,
) -> NotificationTestResponse:
    channel = _get_channel_or_404(db, channel_id)
    preview_text = payload.message or f"Test notification for channel {channel.name}"
    return NotificationTestResponse(
        channel_id=channel.id,
        provider=channel.provider,
        preview_text=preview_text,
    )


def list_notification_services(db: Session) -> NotificationServiceListResponse:
    services = db.scalars(select(Service).order_by(Service.environment, Service.name)).all()
    return NotificationServiceListResponse(
        items=[
            NotificationServiceOption(name=service.name, environment=service.environment)
            for service in services
        ]
    )


def scan_and_dispatch_instance_connectivity_notifications(
    db: Session,
    *,
    now: datetime | None = None,
) -> int:
    current_time = _normalize_scan_now(now)
    channels = db.scalars(
        select(NotificationChannel).where(NotificationChannel.enabled.is_(True))
    ).all()
    connectivity_channels = [
        channel
        for channel in channels
        if any(
            event_type in INSTANCE_CONNECTIVITY_EVENT_TYPES
            for event_type in channel.event_types_json
        )
    ]
    if not connectivity_channels:
        return 0

    states = db.scalars(
        select(NotificationInstanceState).where(
            NotificationInstanceState.channel_id.in_(
                [channel.id for channel in connectivity_channels]
            )
        )
    ).all()
    state_by_key = {
        (state.channel_id, state.instance_id): state
        for state in states
    }
    instances = db.scalars(
        select(Instance)
        .options(selectinload(Instance.service))
        .where(Instance.last_seen_at.is_not(None))
        .order_by(Instance.node_name, Instance.instance_id)
    ).all()
    cutoff = online_cutoff(current_time)
    pending_deliveries: list[tuple[NotificationDelivery, str]] = []

    for channel in connectivity_channels:
        for instance in instances:
            service = instance.service
            if not _service_matches_channel(
                channel,
                service_name=service.name,
                service_environment=service.environment,
            ):
                continue

            connectivity = get_instance_connectivity(instance, cutoff=cutoff)
            if connectivity == "never_reported":
                continue

            transition_at = _instance_transition_time(
                instance,
                connectivity=connectivity,
                now=current_time,
            )
            state_key = (channel.id, instance.instance_id)
            state = state_by_key.get(state_key)
            if state is None:
                state = NotificationInstanceState(
                    channel_id=channel.id,
                    instance_id=instance.instance_id,
                    last_connectivity=connectivity,
                    last_transition_at=transition_at,
                )
                db.add(state)
                state_by_key[state_key] = state
                continue

            if state.last_connectivity == connectivity:
                continue

            state.last_connectivity = connectivity
            state.last_transition_at = transition_at

            notification_event = _build_instance_connectivity_notification_event(
                instance,
                service=service,
                connectivity=connectivity,
                now=current_time,
            )
            if notification_event.event_type not in channel.event_types_json:
                continue

            event_type = (
                "instance_online" if connectivity == "online" else "instance_offline"
            )
            delivery = _persist_pending_delivery(
                db,
                channel=channel,
                notification_event=notification_event,
                dedupe_key=instance_connectivity_dedupe_key(
                    str(channel.id),
                    service_name=service.name,
                    service_environment=service.environment,
                    instance_id=str(instance.instance_id),
                    event_type=event_type,
                    occurred_at=notification_event.occurred_at,
                ),
                task_event_id=None,
                scheduled_at=None,
            )
            if delivery is not None:
                pending_deliveries.append((delivery, channel.webhook_url))

    if not pending_deliveries:
        db.commit()
        return 0

    db.commit()
    for delivery, webhook_url in pending_deliveries:
        _dispatch_delivery(db, delivery=delivery, webhook_url=webhook_url)
    return len(pending_deliveries)


def dispatch_runtime_task_event_notifications(
    db: Session,
    *,
    task_events: list[TaskEvent],
) -> int:
    pending_deliveries: list[tuple[NotificationDelivery, str]] = []
    for task_event in task_events:
        notification_event = _build_runtime_notification_event(task_event)
        if notification_event is None:
            continue
        matching_channels = _matching_channels_for_service(
            db,
            service_name=task_event.service.name,
            service_environment=task_event.service.environment,
            event_type=notification_event.event_type,
        )
        for channel in matching_channels:
            delivery = _persist_pending_delivery(
                db,
                channel=channel,
                notification_event=notification_event,
                dedupe_key=raw_task_event_dedupe_key(channel.id, task_event.event_id),
                task_event_id=task_event.event_id,
                scheduled_at=notification_event.scheduled_at,
            )
            if delivery is not None:
                pending_deliveries.append((delivery, channel.webhook_url))

    if not pending_deliveries:
        return 0

    db.commit()
    for delivery, webhook_url in pending_deliveries:
        _dispatch_delivery(db, delivery=delivery, webhook_url=webhook_url)
    return len(pending_deliveries)


def scan_and_dispatch_missed_start_notifications(
    db: Session,
    *,
    now: datetime | None = None,
    min_last_seen_at: datetime | None = None,
) -> int:
    current_time = _normalize_scan_now(now)
    online_service_started_at_by_id = _online_service_started_at_by_id(
        db,
        now=current_time,
        min_last_seen_at=min_last_seen_at,
    )
    channels = db.scalars(
        select(NotificationChannel).where(
            NotificationChannel.enabled.is_(True)
        )
    ).all()
    missed_start_channels = [
        channel for channel in channels if "task_missed_start" in channel.event_types_json
    ]
    if not missed_start_channels:
        return 0

    task_definitions = db.scalars(
        select(TaskDefinition)
        .options(selectinload(TaskDefinition.service))
        .where(TaskDefinition.source_kind.in_(("cron", "interval")))
        .order_by(TaskDefinition.task_name)
    ).all()

    pending_deliveries: list[tuple[NotificationDelivery, str]] = []
    for channel in missed_start_channels:
        for task_definition in task_definitions:
            service = task_definition.service
            online_started_at = online_service_started_at_by_id.get(service.id)
            if online_started_at is None:
                continue
            interval_seconds = _interval_seconds_from_source_config(
                task_definition.source_config_json
            )
            if not _service_matches_channel(
                channel,
                service_name=service.name,
                service_environment=service.environment,
            ):
                continue
            expected_slots = _iter_expected_slots_for_task(
                now=current_time,
                task_definition=task_definition,
                grace_seconds=channel.missed_start_grace_seconds,
                online_started_at=online_started_at,
            )
            for scheduled_at in expected_slots:
                if scheduled_at < online_started_at:
                    continue
                if _task_started_for_scheduled_slot(
                    db,
                    service_id=service.id,
                    task_name=task_definition.task_name,
                    scheduled_at=scheduled_at,
                    interval_seconds=(
                        interval_seconds
                        if task_definition.source_kind == "interval"
                        else None
                    ),
                ):
                    continue
                notification_event = NotificationEventRecord(
                    event_type="task_missed_start",
                    service_name=service.name,
                    service_environment=service.environment,
                    task_name=task_definition.task_name,
                    occurred_at=scheduled_at
                    + timedelta(seconds=channel.missed_start_grace_seconds),
                    scheduled_at=scheduled_at,
                    detected_at=current_time,
                    missed_start_grace_seconds=channel.missed_start_grace_seconds,
                    console_url=settings.build_console_url(
                        f"/services/{service.name}/tasks/{task_definition.task_name}"
                        f"?environment={service.environment}"
                    ),
                )
                delivery = _persist_pending_delivery(
                    db,
                    channel=channel,
                    notification_event=notification_event,
                    dedupe_key=missed_start_dedupe_key(
                        channel.id,
                        service_name=service.name,
                        service_environment=service.environment,
                        task_name=task_definition.task_name,
                        scheduled_at=scheduled_at,
                    ),
                    task_event_id=None,
                    scheduled_at=scheduled_at,
                )
                if delivery is not None:
                    pending_deliveries.append((delivery, channel.webhook_url))
                break

    if not pending_deliveries:
        db.commit()
        return 0

    db.commit()
    for delivery, webhook_url in pending_deliveries:
        _dispatch_delivery(db, delivery=delivery, webhook_url=webhook_url)
    return len(pending_deliveries)
