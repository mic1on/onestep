"""Agent presence beacon endpoint.

The reporter's presence beacon is a daemon thread that POSTs here *only* while
the agent's event loop is blocked. It exists because the heartbeat loop shares
that loop: a synchronous handler (``requests``, ``boto3``, ``pandas``) starves
the heartbeat, ``last_seen_at`` goes stale, and a working instance is reported
``offline`` followed by ``online`` when the block ends. See
``onestep_control_plane.presence`` in the reporter plugin for the client side.

Why HTTP and not the agent WebSocket
------------------------------------
The whole point is to reach the control plane *without* the event loop that
owns the socket. A blocked loop cannot run the WebSocket's ping/pong either, so
the socket itself dies (uvicorn's default 20s ping timeout) before the offline
window elapses.

Database convention
-------------------
``async def`` route that opens its own :func:`session_scope` and takes no
session parameter, per the repository's native-async rule: an ``async def``
handler must never receive a synchronous ``Session``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from onestep_control_plane_api.api.agent_ingestion_service import ingest_presence_request
from onestep_control_plane_api.api.schemas import (
    AgentPresenceRequest,
    IngestionAcceptedResponse,
)
from onestep_control_plane_api.api.security import require_ingest_token
from onestep_control_plane_api.db.session import session_scope

router = APIRouter(prefix="/api/v1/agents", tags=["agent-presence"])


@router.post(
    "/presence",
    response_model=IngestionAcceptedResponse,
    status_code=202,
)
async def ingest_presence(
    request: AgentPresenceRequest,
    _token: str = Depends(require_ingest_token),
) -> IngestionAcceptedResponse:
    """Record that an instance is alive, without touching its telemetry.

    Side effect is exactly one column: ``instances.last_seen_at``. The frame
    carries no sequence and no health, so ``status``, ``app_snapshot_json`` and
    the heartbeat sequence are untouched -- a beacon can never overwrite or
    reorder what the agent's own telemetry reported.

    ``409`` means the ``instance_id`` is already bound to a different
    service/environment; the plugin treats ``4xx`` as fatal and stops its
    beacon, which is the correct outcome for a misconfigured identity.
    """

    async with session_scope() as session:
        return await ingest_presence_request(session, request)
