from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from onestep_control_plane_api.api.notification_service import (
    create_notification_channel,
    delete_notification_channel,
    deliver_notification_test,
    list_notification_channels,
    list_notification_deliveries,
    list_notification_services,
    update_notification_channel,
    update_notification_channel_enabled,
)
from onestep_control_plane_api.api.schemas import (
    NotificationChannelCreateRequest,
    NotificationChannelDeleteResponse,
    NotificationChannelEnabledPatchRequest,
    NotificationChannelListResponse,
    NotificationChannelSummary,
    NotificationChannelUpdateRequest,
    NotificationDeliveryListResponse,
    NotificationServiceListResponse,
    NotificationTestRequest,
    NotificationTestResponse,
)
from onestep_control_plane_api.api.security import (
    require_console_auth,
    require_console_write_access,
)
from onestep_control_plane_api.db.session import get_db_session

router = APIRouter(
    prefix="/api/v1/settings/notifications",
    tags=["notifications"],
    dependencies=[Depends(require_console_auth)],
)


@router.get("/channels", response_model=NotificationChannelListResponse)
def get_notification_channels(
    db: Session = Depends(get_db_session),
) -> NotificationChannelListResponse:
    return NotificationChannelListResponse(items=list_notification_channels(db))


@router.post(
    "/channels",
    response_model=NotificationChannelSummary,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_console_write_access)],
)
def post_notification_channel(
    request: NotificationChannelCreateRequest,
    db: Session = Depends(get_db_session),
) -> NotificationChannelSummary:
    return create_notification_channel(db, request)


@router.patch(
    "/channels/{channel_id}/enabled",
    response_model=NotificationChannelSummary,
    dependencies=[Depends(require_console_write_access)],
)
def patch_notification_channel_enabled(
    channel_id: UUID,
    request: NotificationChannelEnabledPatchRequest,
    db: Session = Depends(get_db_session),
) -> NotificationChannelSummary:
    return update_notification_channel_enabled(db, channel_id, request)


@router.patch(
    "/channels/{channel_id}",
    response_model=NotificationChannelSummary,
    dependencies=[Depends(require_console_write_access)],
)
def patch_notification_channel(
    channel_id: UUID,
    request: NotificationChannelUpdateRequest,
    db: Session = Depends(get_db_session),
) -> NotificationChannelSummary:
    return update_notification_channel(db, channel_id, request)


@router.delete(
    "/channels/{channel_id}",
    response_model=NotificationChannelDeleteResponse,
    dependencies=[Depends(require_console_write_access)],
)
def remove_notification_channel(
    channel_id: UUID,
    db: Session = Depends(get_db_session),
) -> NotificationChannelDeleteResponse:
    delete_notification_channel(db, channel_id)
    return NotificationChannelDeleteResponse()


@router.post(
    "/channels/{channel_id}/test",
    response_model=NotificationTestResponse,
    dependencies=[Depends(require_console_write_access)],
)
def test_notification_channel(
    channel_id: UUID,
    request: NotificationTestRequest,
    db: Session = Depends(get_db_session),
) -> NotificationTestResponse:
    """Send one real notification through the channel and report what happened.

    Runs in the threadpool (sync ``def``), so the blocking webhook call does not
    stall the event loop.
    """

    return deliver_notification_test(db, channel_id, request)


@router.get("/services", response_model=NotificationServiceListResponse)
def get_notification_services(
    db: Session = Depends(get_db_session),
) -> NotificationServiceListResponse:
    return list_notification_services(db)


@router.get("/deliveries", response_model=NotificationDeliveryListResponse)
def get_notification_deliveries(
    channel_id: UUID | None = None,
    limit: int = 50,
    db: Session = Depends(get_db_session),
) -> NotificationDeliveryListResponse:
    """Recent delivery attempts, so an operator can see whether alerts went out."""

    return list_notification_deliveries(db, channel_id=channel_id, limit=limit)
