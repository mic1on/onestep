from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from onestep_control_plane_api.api.schemas import (
    NotificationChannelCreateRequest,
    NotificationChannelSummary,
    NotificationChannelUpdateRequest,
    NotificationServiceListResponse,
    NotificationServiceOption,
    NotificationTestRequest,
    NotificationTestResponse,
)
from onestep_control_plane_api.db.models import NotificationChannel, Service


def _build_channel_summary(channel: NotificationChannel) -> NotificationChannelSummary:
    return NotificationChannelSummary(
        id=channel.id,
        name=channel.name,
        provider=channel.provider,
        webhook_url=channel.webhook_url,
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


def list_notification_channels(db: Session) -> list[NotificationChannelSummary]:
    channels = db.scalars(
        select(NotificationChannel).order_by(NotificationChannel.created_at, NotificationChannel.name)
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
        service_scopes_json=[scope.model_dump() for scope in payload.service_scopes],
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
            scope.model_dump() for scope in update_data["service_scopes"]
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
