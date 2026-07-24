from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DeploymentState:
    deployment_id: str
    runtime_instance_id: str
    package_dir: Path
    entrypoint: str
    env: dict[str, str]
    pid: int | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "deployment_id": self.deployment_id,
            "runtime_instance_id": self.runtime_instance_id,
            "package_dir": str(self.package_dir),
            "entrypoint": self.entrypoint,
            "env": self.env,
            "pid": self.pid,
        }

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> DeploymentState:
        return cls(
            deployment_id=str(payload["deployment_id"]),
            runtime_instance_id=str(payload["runtime_instance_id"]),
            package_dir=Path(str(payload["package_dir"])),
            entrypoint=str(payload["entrypoint"]),
            env={str(key): str(value) for key, value in dict(payload.get("env", {})).items()},
            pid=int(payload["pid"]) if payload.get("pid") is not None else None,
        )


class DeploymentStateStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load_all(self) -> dict[str, DeploymentState]:
        if not self.path.exists():
            return {}
        payload = json.loads(self.path.read_text())
        deployments = payload.get("deployments", [])
        if not isinstance(deployments, list):
            return {}
        states: dict[str, DeploymentState] = {}
        for item in deployments:
            if isinstance(item, dict):
                state = DeploymentState.from_json(item)
                states[state.deployment_id] = state
        return states

    def save_all(self, states: dict[str, DeploymentState]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        tmp_path.write_text(
            json.dumps(
                {
                    "deployments": [
                        state.to_json() for state in sorted(
                            states.values(),
                            key=lambda item: item.deployment_id,
                        )
                    ]
                },
                indent=2,
                sort_keys=True,
            )
        )
        tmp_path.replace(self.path)

    def upsert(self, state: DeploymentState) -> None:
        states = self.load_all()
        states[state.deployment_id] = state
        self.save_all(states)

    def remove(self, deployment_id: str) -> None:
        states = self.load_all()
        if deployment_id not in states:
            return
        states.pop(deployment_id, None)
        self.save_all(states)
