from __future__ import annotations

import os
import sys
from pathlib import Path

import uvicorn
from alembic import command
from alembic.config import Config


def parse_port() -> int:
    raw = os.environ.get("ONESTEP_CP_PORT", "4173")
    try:
        port = int(raw)
    except ValueError:
        print(f"invalid ONESTEP_CP_PORT: {raw!r}", file=sys.stderr)
        raise SystemExit(2)
    if port <= 0 or port > 65535:
        print(f"ONESTEP_CP_PORT out of range: {port}", file=sys.stderr)
        raise SystemExit(2)
    return port


def _default_repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def build_alembic_config() -> Config:
    repo_root = Path(os.environ.get("ONESTEP_CP_REPO_ROOT", _default_repo_root())).resolve()
    ini_path = Path(os.environ.get("ONESTEP_CP_ALEMBIC_INI", repo_root / "alembic.ini")).resolve()
    script_location = Path(
        os.environ.get("ONESTEP_CP_ALEMBIC_SCRIPT_LOCATION", repo_root / "backend" / "alembic")
    ).resolve()
    config = Config(str(ini_path))
    config.set_main_option("script_location", str(script_location))
    return config


def run_migrations() -> None:
    command.upgrade(build_alembic_config(), "head")


def main() -> None:
    host = os.environ.get("ONESTEP_CP_HOST", "127.0.0.1")
    port = parse_port()
    run_migrations()
    uvicorn.run(
        "onestep_control_plane_api.main:app",
        host=host,
        port=port,
        log_level=os.environ.get("ONESTEP_CP_LOG_LEVEL", "info"),
    )


if __name__ == "__main__":
    main()
