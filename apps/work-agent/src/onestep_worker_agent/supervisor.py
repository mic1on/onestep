from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import signal
import sys
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4
from weakref import WeakValueDictionary

from onestep_worker_agent.state import DeploymentState, DeploymentStateStore

#: Name written next to a ready venv so the install step is idempotent and
#: crash-safe: an interrupted install leaves no marker, so the next attempt
#: rebuilds the venv from scratch via pip's own idempotency.
INSTALLED_MARKER = ".installed"
DEFAULT_RUNTIME_REQUIREMENTS = (
    "onestep[all]>=1.4.2",
    "onestep-feishu-bitable>=0.1.2",
)


@dataclass(frozen=True)
class DeploymentSpec:
    deployment_id: str
    worker_agent_id: str
    runtime_instance_id: str
    package_dir: Path
    entrypoint: str
    env: dict[str, str]
    #: Virtualenv directory shared by all deployments of the same package
    #: checksum. ``None`` when the package declares no dependencies and the
    #: deployment runs against the global ``onestep``.
    venv_dir: Path | None = None
    #: Resolved ``onestep`` executable: ``<venv>/bin/onestep`` when a venv is
    #: in use, otherwise the global ``onestep`` discovered via ``shutil.which``.
    onestep_executable: str = "onestep"


class InstallError(RuntimeError):
    """Raised when dependency installation (venv creation or pip install) fails."""


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
        self.venvs_dir = work_dir / "venvs"
        self._reserved: set[str] = set()
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._external_pids: dict[str, int] = {}
        self._log_files: dict[str, object] = {}
        self._locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()
        self._monitors: dict[str, asyncio.Task] = {}
        self._runtime_ids: dict[str, str] = {}
        self._stopping: set[str] = set()
        self.events_path = work_dir / "exit-events.json"
        self.exit_events = (
            json.loads(self.events_path.read_text()) if self.events_path.exists() else {}
        )

    def operation_lock(self, deployment_id: str) -> asyncio.Lock:
        return self._locks.setdefault(deployment_id, asyncio.Lock())

    def runtime_instance_id(self, deployment_id: str) -> str | None:
        self._reap_external()
        return self._runtime_ids.get(deployment_id)

    def _save_events(self) -> None:
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.events_path.with_suffix(".tmp")
        with temporary.open("w") as stream:
            json.dump(self.exit_events, stream)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(self.events_path)

    def acknowledge_exit(self, event_id: str) -> None:
        if self.exit_events.pop(event_id, None) is not None:
            self._save_events()

    def _exited(self, deployment_id: str, returncode: int | None) -> None:
        event_id = str(uuid4())
        self.exit_events[event_id] = {
            "deployment_id": deployment_id,
            "returncode": returncode,
            "runtime_instance_id": self._runtime_ids.get(deployment_id),
        }
        self._save_events()
        self.release_slot(deployment_id)

    def _reap_external(self) -> None:
        for deployment_id, pid in list(self._external_pids.items()):
            if not self._pid_is_alive(pid):
                self._exited(deployment_id, None)

    async def _monitor(self, deployment_id: str, process) -> None:
        returncode = await process.wait()
        if self._processes.get(deployment_id) is process and deployment_id not in self._stopping:
            self._exited(deployment_id, returncode)

    @property
    def used_slots(self) -> int:
        self._reap_external()
        return len(self._reserved)

    def running_deployments(self) -> list[str]:
        self._reap_external()
        return sorted(self._runtime_ids)

    def reserve_slot(self, deployment_id: str) -> None:
        if deployment_id in self._reserved:
            return
        if len(self._reserved) >= self.max_concurrent_deployments:
            raise RuntimeError("no deployment slots available")
        self._reserved.add(deployment_id)

    def release_slot(self, deployment_id: str) -> None:
        self._reserved.discard(deployment_id)
        self._runtime_ids.pop(deployment_id, None)
        self._processes.pop(deployment_id, None)
        self._external_pids.pop(deployment_id, None)
        log_file = self._log_files.pop(deployment_id, None)
        if log_file is not None:
            log_file.close()
        if self.state_store is not None:
            self.state_store.remove(deployment_id)

    def recover_running_deployments(self) -> list[str]:
        if self.state_store is None:
            return []
        recovered: list[str] = []
        for state in self.state_store.load_all().values():
            self._runtime_ids[state.deployment_id] = state.runtime_instance_id
            if state.pid is None or not self._pid_is_alive(state.pid):
                self._exited(state.deployment_id, None)
                continue
            # Existing workers remain managed even if capacity was reduced.
            self._reserved.add(state.deployment_id)
            self._external_pids[state.deployment_id] = state.pid
            recovered.append(state.deployment_id)
        return recovered

    def venv_path_for(self, package_checksum: str) -> Path:
        """Shared venv directory keyed on the package checksum."""
        return self.venvs_dir / package_checksum / "venv"

    def build_environment(self, spec: DeploymentSpec) -> dict[str, str]:
        env = dict(os.environ)
        env.update(spec.env)
        env["ONESTEP_DEPLOYMENT_ID"] = spec.deployment_id
        env["ONESTEP_WORKER_AGENT_ID"] = spec.worker_agent_id
        env["ONESTEP_RUNTIME_INSTANCE_ID"] = spec.runtime_instance_id
        env["ONESTEP_INSTANCE_ID"] = spec.runtime_instance_id
        if spec.venv_dir is not None:
            # Prepend the venv's bin dir so onestep's own subprocesses resolve
            # the same interpreter and installed entry points.
            venv_bin = self._venv_bin_dir(spec.venv_dir)
            env["PATH"] = f"{venv_bin}{os.pathsep}{env.get('PATH', '')}"
            env["VIRTUAL_ENV"] = str(spec.venv_dir)
        return env

    @staticmethod
    def _venv_bin_dir(venv_dir: Path) -> Path:
        return venv_dir / ("Scripts" if os.name == "nt" else "bin")

    @staticmethod
    def _venv_onestep(venv_dir: Path) -> str:
        return str(SubprocessSupervisor._venv_bin_dir(venv_dir) / "onestep")

    @staticmethod
    def detect_install_mode(package_dir: Path) -> str | None:
        """Return the pip install mode for a package, or ``None`` to skip.

        ``pip install .`` when ``pyproject.toml`` is present (an installable
        package), ``pip install -r requirements.txt`` when that file is present,
        or both when the package declares both forms.
        """
        has_pyproject = (package_dir / "pyproject.toml").exists()
        has_requirements = (package_dir / "requirements.txt").exists()
        if has_pyproject and has_requirements:
            return "package+requirements"
        if has_pyproject:
            return "package"
        if has_requirements:
            return "requirements"
        return None

    async def install(
        self,
        spec: DeploymentSpec,
        *,
        mode: str,
        timeout_s: float | None = None,
    ) -> None:
        """Create (or reuse) the venv for ``spec`` and install dependencies.

        Raises :class:`InstallError` on any failure. Idempotent: a venv marked
        ``.installed`` is reused without reinstalling.
        """
        assert spec.venv_dir is not None  # noqa: S101 - caller guarantees this
        venv_dir = spec.venv_dir
        marker = venv_dir.parent / INSTALLED_MARKER
        expected_marker = _installed_marker_content()
        if marker.exists() and marker.read_text() == expected_marker:
            return  # reuse: a prior install of this checksum succeeded

        venv_dir.parent.mkdir(parents=True, exist_ok=True)
        venv_bin = self._venv_bin_dir(venv_dir)

        # Create the venv from the interpreter running the agent so venv's own
        # dependencies match the agent's Python.
        create_returncode = await self._run_subprocess(
            [sys.executable, "-m", "venv", str(venv_dir)],
            cwd=spec.package_dir,
            env={**os.environ},
            timeout_s=timeout_s,
            label="venv create",
        )
        if create_returncode != 0:
            raise InstallError(f"venv creation exited with code {create_returncode}")

        pip = str(venv_bin / "python")
        install_commands: list[list[str]] = [
            [pip, "-m", "pip", "install", *DEFAULT_RUNTIME_REQUIREMENTS],
        ]
        if mode == "runtime":
            pass
        elif mode == "package":
            install_commands.append([pip, "-m", "pip", "install", "."])
        elif mode == "requirements":
            install_commands.append([pip, "-m", "pip", "install", "-r", "requirements.txt"])
        elif mode == "package+requirements":
            install_commands.extend(
                [
                    [pip, "-m", "pip", "install", "."],
                    [pip, "-m", "pip", "install", "-r", "requirements.txt"],
                ]
            )
        else:
            raise InstallError(f"unknown install mode: {mode!r}")

        for install_cmd in install_commands:
            install_returncode = await self._run_subprocess(
                install_cmd,
                cwd=spec.package_dir,
                env={**os.environ},
                timeout_s=timeout_s,
                label="pip install",
            )
            if install_returncode != 0:
                # Keep the partial venv (no marker written) so a retrigger can let
                # pip resume from its own cache; the deployment itself fails.
                raise InstallError(f"pip install exited with code {install_returncode}")

        marker.write_text(expected_marker)

    @staticmethod
    async def _spawn_process(*args, **kwargs):
        # Cancellation may race with the OS creating the child. Finish acquiring
        # its handle before cleaning up; cancelling create_subprocess_exec can
        # otherwise lose that handle completely.
        task = asyncio.create_task(
            asyncio.create_subprocess_exec(*args, **kwargs, start_new_session=(os.name != "nt"))
        )
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            process = await task
            await SubprocessSupervisor._kill_and_wait(process)
            raise

    async def _spawn_preparation(self, *args, **kwargs):
        if os.name != "nt":
            args = (
                sys.executable,
                str(Path(__file__).with_name("_prepare.py")),
                str(os.getpid()),
                *args,
            )
        return await self._spawn_process(*args, **kwargs)

    async def _run_subprocess(
        self,
        cmd: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout_s: float | None,
        label: str,
    ) -> int:
        process = await self._spawn_preparation(
            *cmd,
            cwd=cwd,
            env=env,
        )
        try:
            return await asyncio.wait_for(process.wait(), timeout=timeout_s)
        except TimeoutError:
            await self._kill_and_wait(process)
            raise InstallError(f"{label} timed out after {timeout_s}s") from None
        except asyncio.CancelledError:
            await self._kill_and_wait(process)
            raise

    async def check(self, spec: DeploymentSpec, *, timeout_s: float | None = None) -> int:
        process = await self._spawn_preparation(
            spec.onestep_executable,
            "check",
            spec.entrypoint,
            cwd=spec.package_dir,
            env=self.build_environment(spec),
        )
        try:
            return await asyncio.wait_for(process.wait(), timeout=timeout_s)
        except TimeoutError:
            await self._kill_and_wait(process)
            raise
        except asyncio.CancelledError:
            await self._kill_and_wait(process)
            raise

    @staticmethod
    async def _kill_and_wait(process) -> None:
        try:
            if os.name != "nt" and isinstance(process, asyncio.subprocess.Process):
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            pass
        await process.wait()

    async def start(self, spec: DeploymentSpec) -> asyncio.subprocess.Process:
        async with self.operation_lock(spec.deployment_id):
            if spec.deployment_id in self._processes:
                return self._processes[spec.deployment_id]
            if self.runtime_instance_id(spec.deployment_id) is not None:
                raise RuntimeError("deployment already running in a recovered process")
            return await self._start(spec)

    async def _start(self, spec: DeploymentSpec) -> asyncio.subprocess.Process:
        self.reserve_slot(spec.deployment_id)
        # Capture child output to a file so the pipe never fills (a full PIPE
        # blocks the child forever) and the logs stay available for debugging.
        log_path = spec.package_dir.parent / "worker.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            log_file = log_path.open("ab", buffering=0)
            process = await self._spawn_process(
                sys.executable,
                str(Path(__file__).with_name("_launch.py")),
                spec.onestep_executable,
                "run",
                spec.entrypoint,
                cwd=spec.package_dir,
                env=self.build_environment(spec),
                stdin=asyncio.subprocess.PIPE,
                stdout=log_file,
                stderr=asyncio.subprocess.STDOUT,
            )
        except BaseException:
            if "log_file" in locals():
                log_file.close()
            self.release_slot(spec.deployment_id)
            raise
        self._processes[spec.deployment_id] = process
        self._log_files[spec.deployment_id] = log_file
        try:
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
            if getattr(process, "stdin", None) is not None:
                process.stdin.write(b"1")
                await process.stdin.drain()
                process.stdin.close()
        except BaseException:
            await self._kill_and_wait(process)
            self.release_slot(spec.deployment_id)
            raise
        self._runtime_ids[spec.deployment_id] = spec.runtime_instance_id
        monitor = asyncio.create_task(self._monitor(spec.deployment_id, process))
        self._monitors[spec.deployment_id] = monitor

        def finished(task):
            if self._monitors.get(spec.deployment_id) is task:
                self._monitors.pop(spec.deployment_id, None)

        monitor.add_done_callback(finished)
        return process

    async def stop(self, deployment_id: str, *, grace_seconds: float = 10.0) -> int | None:
        async with self.operation_lock(deployment_id):
            self._stopping.add(deployment_id)
            try:
                process = self._processes.get(deployment_id)
                if process is not None:
                    try:
                        if os.name != "nt" and isinstance(process, asyncio.subprocess.Process):
                            os.killpg(process.pid, signal.SIGTERM)
                        else:
                            process.terminate()
                    except ProcessLookupError:
                        pass
                    try:
                        return await asyncio.wait_for(process.wait(), timeout=grace_seconds)
                    except (TimeoutError, asyncio.CancelledError):
                        await self._kill_and_wait(process)
                        if asyncio.current_task().cancelling():
                            raise
                        return process.returncode
                pid = self._external_pids.get(deployment_id)
                if pid is not None:
                    return await self._terminate_pid(pid, grace_seconds=grace_seconds or 10.0)
                return None
            finally:
                pid = self._external_pids.get(deployment_id)
                if pid is None or not self._pid_is_alive(pid):
                    self.release_slot(deployment_id)
                self._stopping.discard(deployment_id)

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

        def send(sig):
            try:
                if os.name != "nt" and os.getpgid(pid) == pid:
                    os.killpg(pid, sig)
                else:
                    os.kill(pid, sig)
            except ProcessLookupError:
                pass

        send(signal.SIGTERM)
        try:
            deadline = asyncio.get_running_loop().time() + grace_seconds
            while self._pid_is_alive(pid) and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            send(signal.SIGKILL)
            await self._wait_external_exit(pid)
            raise
        if self._pid_is_alive(pid):
            send(signal.SIGKILL)
            await self._wait_external_exit(pid)
        return 0

    async def _wait_external_exit(self, pid: int) -> None:
        deadline = asyncio.get_running_loop().time() + 5
        while self._pid_is_alive(pid):
            if asyncio.get_running_loop().time() >= deadline:
                raise RuntimeError(f"process {pid} has not exited after SIGKILL")
            await asyncio.sleep(0.05)


def resolve_onestep_executable(venv_dir: Path | None) -> str:
    """Pick the onestep binary for a deployment.

    ``<venv>/bin/onestep`` when a venv is in use, otherwise the global
    ``onestep`` resolved via ``shutil.which`` (falling back to the bare name
    so the agent still emits a clear PATH error from the OS).
    """
    if venv_dir is not None:
        return SubprocessSupervisor._venv_onestep(venv_dir)
    return shutil.which("onestep") or "onestep"


def _installed_marker_content() -> str:
    digest = hashlib.sha256("\n".join(DEFAULT_RUNTIME_REQUIREMENTS).encode()).hexdigest()
    return f"default-runtime={digest}\n"
