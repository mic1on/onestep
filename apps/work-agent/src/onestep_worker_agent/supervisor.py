from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DeploymentSpec:
    deployment_id: str
    worker_agent_id: str
    runtime_instance_id: str
    package_dir: Path
    env: dict[str, str]


class SubprocessSupervisor:
    def __init__(self, *, work_dir: Path, max_concurrent_deployments: int) -> None:
        if max_concurrent_deployments < 1:
            raise ValueError("max_concurrent_deployments must be at least 1")
        self.work_dir = work_dir
        self.max_concurrent_deployments = max_concurrent_deployments
        self._reserved: set[str] = set()
        self._processes: dict[str, asyncio.subprocess.Process] = {}

    @property
    def used_slots(self) -> int:
        return len(self._reserved)

    def running_deployments(self) -> list[str]:
        return sorted(self._reserved)

    def reserve_slot(self, deployment_id: str) -> None:
        if deployment_id in self._reserved:
            return
        if len(self._reserved) >= self.max_concurrent_deployments:
            raise RuntimeError("no deployment slots available")
        self._reserved.add(deployment_id)

    def release_slot(self, deployment_id: str) -> None:
        self._reserved.discard(deployment_id)
        self._processes.pop(deployment_id, None)

    def build_environment(self, spec: DeploymentSpec) -> dict[str, str]:
        env = dict(os.environ)
        env.update(spec.env)
        env["ONESTEP_DEPLOYMENT_ID"] = spec.deployment_id
        env["ONESTEP_WORKER_AGENT_ID"] = spec.worker_agent_id
        env["ONESTEP_RUNTIME_INSTANCE_ID"] = spec.runtime_instance_id
        return env

    async def check(self, spec: DeploymentSpec) -> int:
        process = await asyncio.create_subprocess_exec(
            "onestep",
            "check",
            "worker.yaml",
            cwd=spec.package_dir,
            env=self.build_environment(spec),
        )
        return await process.wait()

    async def start(self, spec: DeploymentSpec) -> asyncio.subprocess.Process:
        self.reserve_slot(spec.deployment_id)
        try:
            process = await asyncio.create_subprocess_exec(
                "onestep",
                "run",
                "worker.yaml",
                cwd=spec.package_dir,
                env=self.build_environment(spec),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except Exception:
            self.release_slot(spec.deployment_id)
            raise
        self._processes[spec.deployment_id] = process
        return process

    async def stop(self, deployment_id: str, *, grace_seconds: float = 10.0) -> int | None:
        process = self._processes.get(deployment_id)
        if process is None:
            self.release_slot(deployment_id)
            return None
        process.terminate()
        try:
            return await asyncio.wait_for(process.wait(), timeout=grace_seconds)
        except TimeoutError:
            process.kill()
            return await process.wait()
        finally:
            self.release_slot(deployment_id)
