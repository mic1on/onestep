from __future__ import annotations

import json

import pytest

from onestep_worker_agent.config import (
    StoredAgentConfig,
    load_config,
    load_stored_config,
    save_stored_config,
)


def test_save_and_load_stored_config(tmp_path) -> None:
    config = StoredAgentConfig(
        plane_url="http://control-plane.test",
        registration_token="registration-token",
        work_dir=tmp_path / "agent",
        display_name="agent-1",
        max_concurrent_deployments=2,
    )

    path = save_stored_config(config, tmp_path)

    assert path == tmp_path / "config.json"
    assert load_stored_config(tmp_path) == config
    assert path.stat().st_mode & 0o777 == 0o600


def test_load_config_uses_file_with_environment_overrides(tmp_path, monkeypatch) -> None:
    save_stored_config(
        StoredAgentConfig(
            plane_url="http://control-plane.test",
            registration_token="registration-token",
            work_dir=tmp_path / "agent",
            display_name="agent-1",
            max_concurrent_deployments=2,
        ),
        tmp_path,
    )
    monkeypatch.setenv("ONESTEP_PLANE_URL", "http://override.test")
    monkeypatch.setenv("ONESTEP_WORKER_AGENT_MAX_CONCURRENCY", "3")

    config = load_config(tmp_path)

    assert config.plane_url == "http://override.test"
    assert config.registration_token == "registration-token"
    assert config.work_dir == tmp_path / "agent"
    assert config.identity_path == tmp_path / "agent" / "identity.json"
    assert config.display_name == "agent-1"
    assert config.max_concurrent_deployments == 3


def test_load_stored_config_rejects_missing_required_fields(tmp_path) -> None:
    (tmp_path / "config.json").write_text(
        json.dumps({"plane_url": "http://control-plane.test"}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="registration_token"):
        load_stored_config(tmp_path)
