from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AgentConfig:
    plane_url: str
    registration_token: str
    work_dir: Path
    identity_path: Path
    display_name: str
    max_concurrent_deployments: int


def load_config_from_env() -> AgentConfig:
    plane_url = os.environ["ONESTEP_PLANE_URL"].rstrip("/")
    work_dir = Path(os.environ.get("ONESTEP_WORKER_AGENT_DIR", ".onestep-worker-agent"))
    return AgentConfig(
        plane_url=plane_url,
        registration_token=os.environ.get("ONESTEP_AGENT_REGISTRATION_TOKEN", ""),
        work_dir=work_dir,
        identity_path=work_dir / "identity.json",
        display_name=os.environ.get("ONESTEP_WORKER_AGENT_NAME", "worker-agent"),
        max_concurrent_deployments=int(
            os.environ.get("ONESTEP_WORKER_AGENT_MAX_CONCURRENCY", "1")
        ),
    )
