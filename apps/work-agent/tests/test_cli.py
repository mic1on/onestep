from __future__ import annotations

import json
from types import SimpleNamespace

import onestep_worker_agent.cli as cli_module
from onestep_worker_agent.cli import main


def test_setup_writes_config_without_starting(tmp_path, capsys) -> None:
    exit_code = main(
        [
            "setup",
            "--config-dir",
            str(tmp_path),
            "--plane-url",
            "http://control-plane.test",
            "--registration-token",
            "registration-token",
            "--name",
            "agent-1",
            "--work-dir",
            str(tmp_path / "state"),
            "--max-concurrency",
            "2",
            "--no-start",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert f"worker agent config written: {tmp_path / 'config.json'}" in captured.out
    assert "Plane URL: http://control-plane.test" in captured.out
    payload = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert payload == {
        "plane_url": "http://control-plane.test",
        "registration_token": "registration-token",
        "work_dir": str(tmp_path / "state"),
        "display_name": "agent-1",
        "max_concurrent_deployments": 2,
    }


def test_setup_reuses_existing_config_without_force(tmp_path, capsys) -> None:
    existing = tmp_path / "config.json"
    existing.write_text(
        json.dumps(
            {
                "plane_url": "http://existing.test",
                "registration_token": "existing-token",
                "work_dir": str(tmp_path / "existing-state"),
                "display_name": "existing-agent",
                "max_concurrent_deployments": 1,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    exit_code = main(
        [
            "setup",
            "--config-dir",
            str(tmp_path),
            "--plane-url",
            "http://new.test",
            "--registration-token",
            "new-token",
            "--no-start",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert f"worker agent config exists: {existing}" in captured.out
    payload = json.loads(existing.read_text(encoding="utf-8"))
    assert payload["plane_url"] == "http://existing.test"
    assert payload["registration_token"] == "existing-token"


def test_setup_requires_registration_token_when_non_interactive(tmp_path, capsys) -> None:
    exit_code = main(
        [
            "setup",
            "--config-dir",
            str(tmp_path),
            "--plane-url",
            "http://control-plane.test",
            "--no-start",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "Registration token is required" in captured.err


def test_run_executes_foreground_control_loop(monkeypatch, tmp_path) -> None:
    calls: list[object] = []

    async def fake_run(*, config_dir=None):
        calls.append(config_dir)

    monkeypatch.setattr(cli_module, "run", fake_run)

    exit_code = main(["run", "--config-dir", str(tmp_path)])

    assert exit_code == 0
    assert calls == [tmp_path]


def test_start_launches_background_run_process(monkeypatch, tmp_path, capsys) -> None:
    existing = tmp_path / "config.json"
    work_dir = tmp_path / "state"
    existing.write_text(
        json.dumps(
            {
                "plane_url": "http://control-plane.test",
                "registration_token": "registration-token",
                "work_dir": str(work_dir),
                "display_name": "agent-1",
                "max_concurrent_deployments": 2,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    popen_calls: list[dict[str, object]] = []

    class FakeProcess:
        pid = 12345

        def poll(self):
            return None

    def fake_popen(command, **kwargs):
        popen_calls.append({"command": command, **kwargs})
        return FakeProcess()

    monkeypatch.setattr(cli_module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(cli_module.time, "sleep", lambda _delay: None)

    exit_code = main(["start", "--config-dir", str(tmp_path)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert len(popen_calls) == 1
    command = popen_calls[0]["command"]
    assert command == [
        cli_module.sys.executable,
        "-m",
        "onestep_worker_agent.cli",
        "run",
        "--config-dir",
        str(tmp_path),
    ]
    assert popen_calls[0]["stderr"] is cli_module.subprocess.STDOUT
    assert popen_calls[0]["close_fds"] is True
    assert popen_calls[0]["start_new_session"] is True
    assert f"worker agent started in background: pid {FakeProcess.pid}" in captured.out
    assert f"Log: {work_dir / 'agent.log'}" in captured.out


def test_start_reports_background_process_that_exits_immediately(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "plane_url": "http://control-plane.test",
                "registration_token": "registration-token",
                "work_dir": str(tmp_path / "state"),
                "display_name": "agent-1",
                "max_concurrent_deployments": 2,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        cli_module.subprocess,
        "Popen",
        lambda *_args, **_kwargs: SimpleNamespace(pid=12345, poll=lambda: 7),
    )
    monkeypatch.setattr(cli_module.time, "sleep", lambda _delay: None)

    exit_code = main(["start", "--config-dir", str(tmp_path)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "background start failed with exit code 7" in captured.err
