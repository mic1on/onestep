from __future__ import annotations

from pathlib import Path

from onestep_control_plane_api import desktop_entry


def test_build_alembic_config_uses_env_paths(monkeypatch, tmp_path: Path) -> None:
    ini = tmp_path / "alembic.ini"
    migrations = tmp_path / "backend" / "alembic"
    ini.write_text("[alembic]\nscript_location = backend/alembic\n", encoding="utf-8")
    migrations.mkdir(parents=True)

    monkeypatch.setenv("ONESTEP_CP_ALEMBIC_INI", str(ini))
    monkeypatch.setenv("ONESTEP_CP_ALEMBIC_SCRIPT_LOCATION", str(migrations))

    config = desktop_entry.build_alembic_config()

    assert config.config_file_name == str(ini)
    assert config.get_main_option("script_location") == str(migrations)


def test_parse_port_defaults_to_4173(monkeypatch) -> None:
    monkeypatch.delenv("ONESTEP_CP_PORT", raising=False)

    assert desktop_entry.parse_port() == 4173


def test_parse_port_rejects_invalid_value(monkeypatch) -> None:
    monkeypatch.setenv("ONESTEP_CP_PORT", "not-a-port")

    try:
        desktop_entry.parse_port()
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("expected SystemExit")
