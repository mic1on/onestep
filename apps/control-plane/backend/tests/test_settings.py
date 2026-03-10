import pytest
from pydantic import ValidationError
from onestep_control_plane_api.core.settings import Settings


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
        ("http://localhost:5173,http://192.168.1.214:5173", ["http://localhost:5173", "http://192.168.1.214:5173"]),
        ('["http://localhost:5173","http://192.168.1.214:5173"]', ["http://localhost:5173", "http://192.168.1.214:5173"]),
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
