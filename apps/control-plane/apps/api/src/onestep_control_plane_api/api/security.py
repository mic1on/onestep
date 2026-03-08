from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from onestep_control_plane_api.core.settings import settings

bearer_scheme = HTTPBearer(
    scheme_name="IngestBearerAuth",
    description=(
        "Bearer token used by OneStep reporters to push heartbeat, metrics, events, and sync."
    ),
    auto_error=False,
)


def require_ingest_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(bearer_scheme)],
) -> str:
    if not settings.ingest_tokens:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ingestion authentication is not configured",
        )

    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    for token in settings.ingest_tokens:
        if secrets.compare_digest(credentials.credentials, token):
            return credentials.credentials

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="invalid bearer token",
        headers={"WWW-Authenticate": "Bearer"},
    )
