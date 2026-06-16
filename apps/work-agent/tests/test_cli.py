from __future__ import annotations

import json

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
