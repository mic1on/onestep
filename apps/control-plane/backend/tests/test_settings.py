import pytest
from onestep_control_plane_api.core.settings import DEFAULT_DATABASE_URL, Settings
from pydantic import ValidationError


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        ("dev-token", ["dev-token"]),
        ("token-a,token-b", ["token-a", "token-b"]),
        ('["token-a","token-b"]', ["token-a", "token-b"]),
    ],
)
def test_settings_parse_ingest_tokens_from_env(monkeypatch, raw_value, expected) -> None:
    monkeypatch.setenv("ONESTEP_CP_INGEST_TOKENS", raw_value)

    settings = Settings(_env_file=None)

    assert settings.ingest_tokens == expected
    assert settings.ingest_auth_configured is True


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        ("*", ["*"]),
        (
            "http://localhost:5173,http://192.168.1.214:5173",
            ["http://localhost:5173", "http://192.168.1.214:5173"],
        ),
        (
            '["http://localhost:5173","http://192.168.1.214:5173"]',
            ["http://localhost:5173", "http://192.168.1.214:5173"],
        ),
    ],
)
def test_settings_parse_cors_allow_origins_from_env(monkeypatch, raw_value, expected) -> None:
    monkeypatch.setenv("ONESTEP_CP_CORS_ALLOW_ORIGINS", raw_value)

    settings = Settings(_env_file=None)

    assert settings.cors_allow_origins == expected


def test_settings_parse_blank_ingest_tokens_from_env(monkeypatch) -> None:
    monkeypatch.setenv("ONESTEP_CP_INGEST_TOKENS", "   ")

    settings = Settings(_env_file=None)

    assert settings.ingest_tokens == []
    assert settings.ingest_auth_configured is False


def test_settings_default_cors_allow_origins_is_empty(monkeypatch) -> None:
    monkeypatch.delenv("ONESTEP_CP_CORS_ALLOW_ORIGINS", raising=False)

    settings = Settings(_env_file=None)

    assert settings.cors_allow_origins == []


def test_settings_parse_console_auth_pair(monkeypatch) -> None:
    monkeypatch.setenv("ONESTEP_CP_CONSOLE_AUTH_USERNAME", "admin")
    monkeypatch.setenv("ONESTEP_CP_CONSOLE_AUTH_PASSWORD", "secret-pass")

    settings = Settings(_env_file=None)

    assert settings.console_auth_configured is True
    assert settings.console_auth_username == "admin"


def test_settings_reject_partial_console_auth_configuration(monkeypatch) -> None:
    monkeypatch.setenv("ONESTEP_CP_CONSOLE_AUTH_USERNAME", "admin")
    monkeypatch.delenv("ONESTEP_CP_CONSOLE_AUTH_PASSWORD", raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_settings_blank_console_auth_is_disabled(monkeypatch) -> None:
    monkeypatch.setenv("ONESTEP_CP_CONSOLE_AUTH_USERNAME", "   ")
    monkeypatch.setenv("ONESTEP_CP_CONSOLE_AUTH_PASSWORD", "   ")

    settings = Settings(_env_file=None)

    assert settings.console_auth_configured is False


def test_settings_reject_health_participation_window_shorter_than_offline_window(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ONESTEP_CP_INSTANCE_OFFLINE_AFTER_S", "90")
    monkeypatch.setenv("ONESTEP_CP_INSTANCE_HEALTH_PARTICIPATION_WINDOW_S", "30")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_settings_replace_blank_database_url_with_default() -> None:
    settings = Settings(database_url="")

    assert settings.database_url == DEFAULT_DATABASE_URL


def test_settings_parse_readiness_task_stale_after_seconds(monkeypatch) -> None:
    monkeypatch.setenv("ONESTEP_CP_READINESS_TASK_STALE_AFTER_S", "180")

    settings = Settings(_env_file=None)

    assert settings.readiness_task_stale_after_s == 180


def test_settings_reject_too_small_readiness_task_stale_after_seconds(monkeypatch) -> None:
    monkeypatch.setenv("ONESTEP_CP_READINESS_TASK_STALE_AFTER_S", "1")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_settings_parse_background_worker_leader_poll_interval(monkeypatch) -> None:
    monkeypatch.setenv("ONESTEP_CP_BACKGROUND_WORKER_LEADER_POLL_INTERVAL_S", "9")

    settings = Settings(_env_file=None)

    assert settings.background_worker_leader_poll_interval_s == 9


def test_settings_reject_too_small_background_worker_leader_poll_interval(monkeypatch) -> None:
    monkeypatch.setenv("ONESTEP_CP_BACKGROUND_WORKER_LEADER_POLL_INTERVAL_S", "0")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_settings_parse_retention_configuration(monkeypatch) -> None:
    monkeypatch.setenv("ONESTEP_CP_RETENTION_TASK_EVENTS_DAYS", "14")
    monkeypatch.setenv("ONESTEP_CP_RETENTION_TASK_METRIC_WINDOWS_DAYS", "60")
    monkeypatch.setenv("ONESTEP_CP_RETENTION_AGENT_COMMANDS_DAYS", "21")
    monkeypatch.setenv("ONESTEP_CP_RETENTION_DELETE_BATCH_SIZE", "250")

    settings = Settings(_env_file=None)

    assert settings.retention_task_events_days == 14
    assert settings.retention_task_metric_windows_days == 60
    assert settings.retention_agent_commands_days == 21
    assert settings.retention_delete_batch_size == 250


def test_settings_reject_invalid_retention_configuration(monkeypatch) -> None:
    monkeypatch.setenv("ONESTEP_CP_RETENTION_TASK_EVENTS_DAYS", "0")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_settings_build_console_url_uses_base_url_for_relative_path() -> None:
    settings = Settings(console_base_url="https://cp.example")

    assert settings.build_console_url("/services/foo/tasks/bar?environment=prod") == (
        "https://cp.example/services/foo/tasks/bar?environment=prod"
    )


def test_settings_build_console_url_keeps_relative_path_without_base_url() -> None:
    settings = Settings()

    assert settings.build_console_url("/services/foo/tasks/bar?environment=prod") == (
        "/services/foo/tasks/bar?environment=prod"
    )
