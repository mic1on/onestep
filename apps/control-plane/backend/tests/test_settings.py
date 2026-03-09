import pytest
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
