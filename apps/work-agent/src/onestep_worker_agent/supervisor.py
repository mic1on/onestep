from __future__ import annotations

import asyncio
import os
import signal
from dataclasses import dataclass
from pathlib import Path

from onestep_worker_agent.state import DeploymentState, DeploymentStateStore


@dataclass(frozen=True)
class DeploymentSpec:
    deployment_id: str
    worker_agent_id: str
    runtime_instance_id: str
    package_dir: Path
    entrypoint: str
    env: dict[str, str]


class SubprocessSupervisor:
    def __init__(
        self,
        *,
        work_dir: Path,
        max_concurrent_deployments: int,
        state_store: DeploymentStateStore | None = None,
    ) -> None:
        if max_concurrent_deployments < 1:
            raise ValueError("max_concurrent_deployments must be at least 1")
        self.work_dir = work_dir
        self.max_concurrent_deployments = max_concurrent_deployments
        self.state_store = state_store
        self._reserved: set[str] = set()
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._external_pids: dict[str, int] = {}

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
        self._external_pids.pop(deployment_id, None)
        if self.state_store is not None:
            self.state_store.remove(deployment_id)

    def recover_running_deployments(self) -> list[str]:
        if self.state_store is None:
            return []
        recovered: list[str] = []
        for state in self.state_store.load_all().values():
            if state.pid is None or not self._pid_is_alive(state.pid):
                self.state_store.remove(state.deployment_id)
                continue
            try:
                self.reserve_slot(state.deployment_id)
            except RuntimeError:
                continue
            self._external_pids[state.deployment_id] = state.pid
            recovered.append(state.deployment_id)
        return recovered

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
            spec.entrypoint,
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
                spec.entrypoint,
                cwd=spec.package_dir,
                env=self.build_environment(spec),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except Exception:
            self.release_slot(spec.deployment_id)
            raise
        self._processes[spec.deployment_id] = process
        if self.state_store is not None:
            self.state_store.upsert(
                DeploymentState(
                    deployment_id=spec.deployment_id,
                    runtime_instance_id=spec.runtime_instance_id,
                    package_dir=spec.package_dir,
                    entrypoint=spec.entrypoint,
                    env=spec.env,
                    pid=process.pid,
                )
            )
        return process

    async def stop(self, deployment_id: str, *, grace_seconds: float = 10.0) -> int | None:
        process = self._processes.get(deployment_id)
        if process is not None:
            process.terminate()
            try:
                return await asyncio.wait_for(process.wait(), timeout=grace_seconds)
            except TimeoutError:
                process.kill()
                return await process.wait()
            finally:
                self.release_slot(deployment_id)

        pid = self._external_pids.get(deployment_id)
        if pid is not None:
            try:
                return await self._terminate_pid(pid, grace_seconds=grace_seconds)
            finally:
                self.release_slot(deployment_id)

        if process is None:
            self.release_slot(deployment_id)
            return None

    def _pid_is_alive(self, pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    async def _terminate_pid(self, pid: int, *, grace_seconds: float) -> int | None:
        if not self._pid_is_alive(pid):
            return None
        os.kill(pid, signal.SIGTERM)
        deadline = asyncio.get_running_loop().time() + grace_seconds
        while asyncio.get_running_loop().time() < deadline:
            if not self._pid_is_alive(pid):
                return 0
            await asyncio.sleep(0.1)
        if self._pid_is_alive(pid):
            os.kill(pid, signal.SIGKILL)
        return 0
