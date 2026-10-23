from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import replace
from uuid import UUID

import pytest
from test_client import FakeHttpClient, FakeWebSocket, _build_zip, _config

from onestep_worker_agent.client import handle_control_message
from onestep_worker_agent.commands import CommandJournal
from onestep_worker_agent.identity import AgentIdentity
from onestep_worker_agent.state import DeploymentStateStore
from onestep_worker_agent.supervisor import DeploymentSpec, SubprocessSupervisor


def supervisor_at(path):
    return SubprocessSupervisor(
        work_dir=path,
        max_concurrent_deployments=1,
        state_store=DeploymentStateStore(path / "deployments.json"),
    )


def spec_at(path, script="import time; time.sleep(60)"):
    executable = path / "worker"
    executable.write_text(f"#!{sys.executable}\n{script}\n")
    executable.chmod(0o755)
    return DeploymentSpec(
        deployment_id="d",
        worker_agent_id="a",
        runtime_instance_id="r",
        package_dir=path,
        entrypoint="worker.yaml",
        env={},
        onestep_executable=str(executable),
    )


@pytest.mark.parametrize("code", [0, 7])
def test_real_worker_exit_releases_slot_and_persists_exit(tmp_path, code):
    async def run():
        supervisor = supervisor_at(tmp_path)
        process = await supervisor.start(spec_at(tmp_path, f"raise SystemExit({code})"))
        await process.wait()
        await asyncio.sleep(0)
        assert supervisor.used_slots == 0
        assert supervisor.running_deployments() == []
        assert supervisor.state_store.load_all() == {}
        assert next(iter(supervisor.exit_events.values()))["returncode"] == code
        assert supervisor._log_files == {}
        await supervisor.stop("d")
        supervisor.reserve_slot("other")
        restored = supervisor_at(tmp_path)
        assert restored.exit_events == supervisor.exit_events

    asyncio.run(run())


def test_concurrent_start_creates_one_os_worker_and_stop_is_idempotent(tmp_path):
    async def run():
        supervisor = supervisor_at(tmp_path)
        first, second = await asyncio.gather(
            supervisor.start(spec_at(tmp_path)),
            supervisor.start(spec_at(tmp_path)),
        )
        assert first.pid == second.pid
        assert supervisor.used_slots == 1
        await asyncio.gather(supervisor.stop("d"), supervisor.stop("d"))
        assert first.returncode is not None
        assert supervisor.used_slots == 0

    asyncio.run(run())


@pytest.mark.parametrize("kind", ["install", "check"])
def test_cancelling_preparation_reaps_real_subprocess(tmp_path, kind):
    async def run():
        supervisor = supervisor_at(tmp_path)
        pid_file = tmp_path / "pid"
        script = (
            f"import os,time; open({str(pid_file)!r},'w').write(str(os.getpid())); time.sleep(60)"
        )
        if kind == "install":
            task = asyncio.create_task(
                supervisor._run_subprocess(
                    [sys.executable, "-c", script],
                    cwd=tmp_path,
                    env=dict(os.environ),
                    timeout_s=None,
                    label="install",
                )
            )
        else:
            task = asyncio.create_task(supervisor.check(spec_at(tmp_path, script)))
        for _ in range(200):
            if pid_file.exists():
                break
            await asyncio.sleep(0.01)
        pid = int(pid_file.read_text())
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)

    asyncio.run(run())


def test_recovered_pid_exit_releases_slot(tmp_path):
    async def run():
        original = supervisor_at(tmp_path)
        process = await original.start(spec_at(tmp_path))
        recovered = supervisor_at(tmp_path)
        assert recovered.recover_running_deployments() == ["d"]
        process.kill()
        await process.wait()
        assert recovered.running_deployments() == []
        assert recovered.used_slots == 0
        assert recovered.exit_events

    asyncio.run(run())


def command_context(tmp_path):
    import hashlib

    package = _build_zip()
    config = _config(tmp_path)
    identity = AgentIdentity(worker_agent_id=UUID(int=1), connection_token="token")
    message = {
        "type": "command",
        "payload": {
            "command_id": "command",
            "kind": "start_deployment",
            "args": {
                "deployment_id": "d",
                "package_checksum": hashlib.sha256(package).hexdigest(),
                "download_url": "/package",
                "entrypoint": "worker.yaml",
                "env": {},
            },
        },
    }
    return dict(
        config=config, identity=identity, message=message, http_client=FakeHttpClient(package)
    )


@pytest.mark.parametrize("stage", ["download", "install", "check"])
def test_disconnect_during_preparation_cleans_and_replays_cancelled_result(tmp_path, stage):
    async def run():
        supervisor = supervisor_at(tmp_path)
        started = asyncio.Event()

        async def install(*args, **kwargs):
            started.set()
            await asyncio.Future()

        context = command_context(tmp_path)
        if stage == "download":

            async def get(*args, **kwargs):
                context["http_client"].requests.append((args, kwargs))
                await install()

            context["http_client"].get = get
        elif stage == "install":
            supervisor.install = install
        else:

            async def installed(*args, **kwargs):
                pass

            supervisor.install = installed
            supervisor.check = install
        socket = FakeWebSocket()
        task = asyncio.create_task(
            handle_control_message(
                websocket=socket,
                supervisor=supervisor,
                **context,
            )
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert supervisor.used_slots == 0
        assert socket.messages[-1]["payload"]["status"] == "cancelled"
        recovered = supervisor_at(tmp_path)
        replay = FakeWebSocket()
        await handle_control_message(websocket=replay, supervisor=recovered, **context)
        assert replay.messages[-1]["payload"]["status"] == "cancelled"
        assert len(context["http_client"].requests) == 1

    asyncio.run(run())


def test_result_loss_and_agent_restart_never_launch_second_worker(tmp_path):
    async def run():
        supervisor = supervisor_at(tmp_path)
        context = command_context(tmp_path)
        executable = spec_at(tmp_path).onestep_executable

        async def install(*args, **kwargs):
            pass

        async def check(*args, **kwargs):
            return 0

        real_start = supervisor.start

        async def start(spec):
            return await real_start(replace(spec, onestep_executable=executable))

        supervisor.install, supervisor.check, supervisor.start = install, check, start

        class BrokenSocket:
            async def send(self, text):
                raise ConnectionError("disconnected")

        await handle_control_message(websocket=BrokenSocket(), supervisor=supervisor, **context)
        process = supervisor._processes["d"]
        try:
            recovered = supervisor_at(tmp_path)
            recovered.recover_running_deployments()
            socket = FakeWebSocket()
            await handle_control_message(websocket=socket, supervisor=recovered, **context)
            assert socket.messages[-1]["payload"]["status"] == "succeeded"
            assert recovered._external_pids["d"] == process.pid
            assert len(context["http_client"].requests) == 1
            another = {
                **context,
                "message": {
                    **context["message"],
                    "payload": {**context["message"]["payload"], "command_id": "another-start"},
                },
            }
            await handle_control_message(websocket=socket, supervisor=recovered, **another)
            assert socket.messages[-1]["payload"]["result"]["runtime_instance_id"] == (
                supervisor.runtime_instance_id("d")
            )
            assert len(context["http_client"].requests) == 1
            # Also exercise the crash between durable process state and result.
            journal = CommandJournal(tmp_path / "commands")
            record = journal.load("command")
            record["frames"].pop("command_result")
            journal.save(record)
            second_recovery = supervisor_at(tmp_path)
            second_recovery.recover_running_deployments()
            await handle_control_message(websocket=socket, supervisor=second_recovery, **context)
            assert socket.messages[-1]["payload"]["status"] == "succeeded"
            assert len(context["http_client"].requests) == 1
        finally:
            await supervisor.stop("d")

    asyncio.run(run())


def test_spawn_gate_prevents_business_execution_if_state_save_fails(tmp_path):
    async def run():
        supervisor = supervisor_at(tmp_path)
        marker = tmp_path / "business-started"
        spec = spec_at(tmp_path, f"open({str(marker)!r}, 'w').write('started')")

        def fail_save(state):
            raise OSError("disk full")

        supervisor.state_store.upsert = fail_save
        with pytest.raises(OSError, match="disk full"):
            await supervisor.start(spec)
        assert not marker.exists()
        assert supervisor.used_slots == 0
        assert supervisor._processes == {}

    asyncio.run(run())


def test_cancelling_stop_of_recovered_pid_keeps_handle_until_exit(tmp_path):
    async def run():
        supervisor = supervisor_at(tmp_path)
        ready = tmp_path / "ready"
        spec = spec_at(
            tmp_path,
            "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            f"open({str(ready)!r},'w').write('yes'); time.sleep(60)",
        )
        process = await supervisor.start(spec)
        for _ in range(200):
            if ready.exists():
                break
            await asyncio.sleep(0.01)
        recovered = supervisor_at(tmp_path)
        recovered.recover_running_deployments()
        task = asyncio.create_task(recovered.stop("d"))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await process.wait()
        assert recovered.used_slots == 0
        assert recovered._external_pids == {}

    asyncio.run(run())


@pytest.mark.skipif(os.name == "nt", reason="POSIX parent-death process-group supervision")
def test_agent_hard_exit_terminates_preparation_process(tmp_path):
    async def run():
        pid_file = tmp_path / "preparation-pid"
        inner = (
            f"import os,time; open({str(pid_file)!r},'w').write(str(os.getpid())); time.sleep(60)"
        )
        program = f"""
import asyncio, os, sys
from pathlib import Path
from onestep_worker_agent.supervisor import SubprocessSupervisor
async def run():
    supervisor = SubprocessSupervisor(
        work_dir=Path({str(tmp_path)!r}), max_concurrent_deployments=1)
    asyncio.create_task(supervisor._run_subprocess([sys.executable, '-c', {inner!r}],
        cwd=Path({str(tmp_path)!r}), env=dict(os.environ), timeout_s=None, label='pip'))
    while not Path({str(pid_file)!r}).exists():
        await asyncio.sleep(0.01)
    os._exit(17)
asyncio.run(run())
"""
        agent = await asyncio.create_subprocess_exec(sys.executable, "-c", program)
        assert await agent.wait() == 17
        pid = int(pid_file.read_text())
        for _ in range(300):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            await asyncio.sleep(0.01)
        else:
            os.kill(pid, 9)
            pytest.fail("preparation child survived agent death")

    asyncio.run(run())


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups")
@pytest.mark.parametrize("recovered", [False, True, "dead_leader"])
def test_stop_kills_descendant_when_group_leader_exits_on_term(tmp_path, recovered):
    async def run():
        supervisor = supervisor_at(tmp_path)
        ready = tmp_path / "descendant"
        child = (
            "import os,signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            f"open({str(ready)!r},'w').write(str(os.getpid())); time.sleep(60)"
        )
        parent = (
            "import subprocess,sys,time; "
            f"subprocess.Popen([sys.executable,'-c',{child!r}]); time.sleep(60)"
        )
        process = await supervisor.start(spec_at(tmp_path, parent))
        child_pid = None
        try:
            for _ in range(300):
                if ready.exists():
                    break
                await asyncio.sleep(0.01)
            child_pid = int(ready.read_text())
            owner = supervisor
            if recovered:
                # Simulate the original agent no longer having a monitor.
                monitor = supervisor._monitors["d"]
                monitor.cancel()
                await asyncio.gather(monitor, return_exceptions=True)
                if recovered == "dead_leader":
                    process.kill()
                    await process.wait()
                owner = supervisor_at(tmp_path)
                assert owner.recover_running_deployments() == ["d"]
            await owner.stop("d", grace_seconds=0.1)
            await process.wait()
            assert owner.used_slots == 0
            assert owner.state_store.load_all() == {}
            with pytest.raises(ProcessLookupError):
                os.kill(child_pid, 0)
        finally:
            if child_pid is not None:
                try:
                    os.kill(child_pid, 9)
                except ProcessLookupError:
                    pass
            if process.returncode is None:
                process.kill()
                await process.wait()

    asyncio.run(run())


def test_cancelled_stop_event_send_reports_already_completed_stop(tmp_path):
    async def run():
        supervisor = supervisor_at(tmp_path)
        process = await supervisor.start(spec_at(tmp_path))
        context = command_context(tmp_path)
        context["message"]["payload"]["kind"] = "stop_deployment"
        stopped = asyncio.Event()

        class BlockingSocket(FakeWebSocket):
            async def send(self, text):
                frame = json.loads(text)
                if (
                    frame["type"] == "deployment_event"
                    and frame["payload"]["observed_status"] == "stopped"
                ):
                    stopped.set()
                    await asyncio.Future()
                await super().send(text)

        socket = BlockingSocket()
        task = asyncio.create_task(
            handle_control_message(websocket=socket, supervisor=supervisor, **context)
        )
        await asyncio.wait_for(stopped.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert process.returncode is not None
        assert socket.messages[-1]["payload"]["status"] == "succeeded"
        assert socket.messages[-1]["payload"]["error_code"] is None
        record = CommandJournal(tmp_path / "commands").load("command")
        assert record["frames"]["command_result"]["payload"]["status"] == "succeeded"

    asyncio.run(run())


@pytest.mark.skipif(os.name == "nt", reason="POSIX legacy shared process group")
def test_legacy_recovered_pid_does_not_signal_agents_shared_group(tmp_path):
    from onestep_worker_agent.state import DeploymentState

    async def run():
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-c", "import time; time.sleep(60)"
        )
        supervisor = supervisor_at(tmp_path)
        supervisor.state_store.upsert(
            DeploymentState(
                deployment_id="legacy",
                runtime_instance_id="legacy",
                package_dir=tmp_path,
                entrypoint="worker.yaml",
                env={},
                pid=process.pid,
            )
        )
        try:
            assert os.getpgid(process.pid) == os.getpgrp()
            assert supervisor.recover_running_deployments() == ["legacy"]
            assert "legacy" not in supervisor._process_groups
            await supervisor.stop("legacy", grace_seconds=0.1)
            await process.wait()
            assert supervisor.used_slots == 0
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()

    asyncio.run(run())
