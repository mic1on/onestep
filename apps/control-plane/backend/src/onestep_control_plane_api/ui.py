from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse, Response

from onestep_control_plane_api.core.settings import settings

router = APIRouter(include_in_schema=False)

RESERVED_FIRST_PATH_SEGMENTS = {
    "api",
    "docs",
    "healthz",
    "openapi.json",
    "readyz",
    "redoc",
}
RESERVED_HTTP_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]


def _ui_dist_dir() -> Path:
    return Path(settings.ui_dist_dir).resolve()


def _safe_file(root: Path, relative_path: str) -> Path:
    resolved_root = root.resolve()
    candidate = (resolved_root / relative_path).resolve()
    if candidate != resolved_root and resolved_root not in candidate.parents:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="asset not found")
    if not candidate.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="asset not found")
    return candidate


def _index_file() -> Path:
    return _safe_file(_ui_dist_dir(), "index.html")


@router.api_route("/api", methods=RESERVED_HTTP_METHODS)
@router.api_route("/api/{reserved_path:path}", methods=RESERVED_HTTP_METHODS)
@router.api_route("/docs", methods=RESERVED_HTTP_METHODS)
@router.api_route("/docs/{reserved_path:path}", methods=RESERVED_HTTP_METHODS)
@router.api_route("/healthz", methods=RESERVED_HTTP_METHODS)
@router.api_route("/healthz/{reserved_path:path}", methods=RESERVED_HTTP_METHODS)
@router.api_route("/openapi.json", methods=RESERVED_HTTP_METHODS)
@router.api_route("/openapi.json/{reserved_path:path}", methods=RESERVED_HTTP_METHODS)
@router.api_route("/readyz", methods=RESERVED_HTTP_METHODS)
@router.api_route("/readyz/{reserved_path:path}", methods=RESERVED_HTTP_METHODS)
@router.api_route("/redoc", methods=RESERVED_HTTP_METHODS)
@router.api_route("/redoc/{reserved_path:path}", methods=RESERVED_HTTP_METHODS)
def reserved_path_not_found() -> None:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")


@router.get("/app-config.js")
def app_config_js() -> Response:
    payload = json.dumps(
        {"apiBaseUrl": settings.ui_api_base_url or "/"},
        ensure_ascii=False,
    )
    return Response(
        content=f"window.__APP_CONFIG__ = {payload};\n",
        media_type="application/javascript",
    )


@router.get("/assets/{asset_path:path}")
def vite_asset(asset_path: str) -> FileResponse:
    return FileResponse(_safe_file(_ui_dist_dir() / "assets", asset_path))


@router.get("/")
def console_index() -> FileResponse:
    return FileResponse(_index_file())


@router.get("/{full_path:path}")
def console_spa_fallback(full_path: str) -> FileResponse:
    first_segment = full_path.split("/", 1)[0]
    if first_segment in RESERVED_FIRST_PATH_SEGMENTS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    return FileResponse(_index_file())
