from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID


@dataclass(frozen=True)
class AgentIdentity:
    worker_agent_id: UUID
    connection_token: str


def load_identity(path: Path) -> AgentIdentity | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text())
    return AgentIdentity(
        worker_agent_id=UUID(payload["worker_agent_id"]),
        connection_token=str(payload["connection_token"]),
    )


def save_identity(path: Path, identity: AgentIdentity) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "worker_agent_id": str(identity.worker_agent_id),
                "connection_token": identity.connection_token,
            },
            indent=2,
            sort_keys=True,
        )
    )
