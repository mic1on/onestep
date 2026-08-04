from __future__ import annotations

import json
import os
import sys
import types
from contextlib import contextmanager

import pytest

from onestep.config import (
    _collect_env_refs,
    _expand_env_var,
    _expand_env_vars,
    _load_dotenv,
    _make_expand_env_var,
    load_yaml_app,
)
from onestep.cli import main


@contextmanager
def _registered_module(name: str, **attrs):
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    previous = sys.modules.get(name)
    sys.modules[name] = module
    try:
        yield module
    finally:
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous


def _yaml_safe_load(stream):
    """Mock yaml.safe_load that handles both file handles and strings."""
    if hasattr(stream, "read"):
        return json.loads(stream.read())
    return json.loads(stream)


@contextmanager
def _registered_yaml_module():
    with _registered_module("yaml", safe_load=_yaml_safe_load, YAMLError=Exception) as module:
        yield module


# ---------------------------------------------------------------------------
# _load_dotenv
# ---------------------------------------------------------------------------

def test_load_dotenv_basic_key_value(tmp_path, monkeypatch):
    monkeypatch.delenv("DB_DSN_1", raising=False)
    monkeypatch.delenv("POOL_SIZE_1", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("DB_DSN_1=mysql://localhost\nPOOL_SIZE_1=20\n", encoding="utf-8")
    loaded = _load_dotenv(str(env_file))
    assert loaded == 2
    assert os.environ["DB_DSN_1"] == "mysql://localhost"
    assert os.environ["POOL_SIZE_1"] == "20"


def test_load_dotenv_double_quoted_value(tmp_path, monkeypatch):
    monkeypatch.delenv("API_TOKEN_2", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text('API_TOKEN_2="abc123"\n', encoding="utf-8")
    loaded = _load_dotenv(str(env_file))
    assert loaded == 1
    assert os.environ["API_TOKEN_2"] == "abc123"


def test_load_dotenv_single_quoted_value(tmp_path, monkeypatch):
    monkeypatch.delenv("API_TOKEN_3", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("API_TOKEN_3='abc123'\n", encoding="utf-8")
    loaded = _load_dotenv(str(env_file))
    assert loaded == 1
    assert os.environ["API_TOKEN_3"] == "abc123"


def test_load_dotenv_skips_comments_and_empty_lines(tmp_path, monkeypatch):
    monkeypatch.delenv("DB_DSN_4", raising=False)
    monkeypatch.delenv("POOL_SIZE_4", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n# DB settings\nDB_DSN_4=mysql://localhost\n\n# Pool\nPOOL_SIZE_4=20\n\n",
        encoding="utf-8",
    )
    loaded = _load_dotenv(str(env_file))
    assert loaded == 2


def test_load_dotenv_value_with_trailing_comment(tmp_path, monkeypatch):
    monkeypatch.delenv("DB_DSN_5", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("DB_DSN_5=mysql://localhost # connection string\n", encoding="utf-8")
    loaded = _load_dotenv(str(env_file))
    assert loaded == 1
    assert os.environ["DB_DSN_5"] == "mysql://localhost"


def test_load_dotenv_quoted_value_preserves_spaces(tmp_path, monkeypatch):
    monkeypatch.delenv("PADDED_TOKEN", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text('PADDED_TOKEN="  abc123  " # comment\n', encoding="utf-8")
    loaded = _load_dotenv(str(env_file))
    assert loaded == 1
    assert os.environ["PADDED_TOKEN"] == "  abc123  "


def test_load_dotenv_shell_variable_takes_precedence(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_DSN", "from-shell")
    env_file = tmp_path / ".env"
    env_file.write_text("DB_DSN=from-dotenv\n", encoding="utf-8")
    loaded = _load_dotenv(str(env_file))
    assert loaded == 0
    assert os.environ["DB_DSN"] == "from-shell"


def test_load_dotenv_file_not_found():
    with pytest.raises(FileNotFoundError):
        _load_dotenv("/nonexistent/.env")


# ---------------------------------------------------------------------------
# _collect_env_refs
# ---------------------------------------------------------------------------

def test_collect_env_refs_missing_variable(monkeypatch):
    monkeypatch.delenv("UNDEFINED_VAR", raising=False)
    config = {"resources": {"db": {"dsn": "${UNDEFINED_VAR}"}}}
    missing = _collect_env_refs(config)
    assert "UNDEFINED_VAR" in missing
    assert "config.resources.db.dsn" in missing["UNDEFINED_VAR"]


def test_collect_env_refs_present_variable(monkeypatch):
    monkeypatch.setenv("PRESENT_VAR", "value")
    config = {"resources": {"db": {"dsn": "${PRESENT_VAR}"}}}
    missing = _collect_env_refs(config)
    assert "PRESENT_VAR" not in missing


def test_collect_env_refs_default_value_skipped(monkeypatch):
    monkeypatch.delenv("TIMEOUT", raising=False)
    config = {"resources": {"api": {"timeout": "${TIMEOUT:-30}"}}}
    missing = _collect_env_refs(config)
    assert len(missing) == 0


def test_collect_env_refs_colon_default_skipped(monkeypatch):
    monkeypatch.delenv("TIMEOUT", raising=False)
    config = {"resources": {"api": {"timeout": "${TIMEOUT:30}"}}}
    missing = _collect_env_refs(config)
    assert len(missing) == 0


def test_collect_env_refs_recursive_list_and_dict(monkeypatch):
    monkeypatch.delenv("A", raising=False)
    monkeypatch.delenv("B", raising=False)
    config = {
        "tasks": [
            {"name": "t1", "config": {"key": "${A}"}},
            {"name": "t2", "config": {"key": "${B}"}},
        ]
    }
    missing = _collect_env_refs(config)
    assert "A" in missing
    assert "B" in missing
    assert "config.tasks[0].config.key" in missing["A"]
    assert "config.tasks[1].config.key" in missing["B"]


def test_collect_env_refs_non_string_values_ignored(monkeypatch):
    config = {"timeout": 30, "enabled": True, "optional": None}
    missing = _collect_env_refs(config)
    assert len(missing) == 0


# ---------------------------------------------------------------------------
# _expand_env_var regression
# ---------------------------------------------------------------------------

def test_expand_env_var_basic(monkeypatch):
    monkeypatch.setenv("FOO", "bar")
    import re
    match = re.compile(r"\$\{([^}]+)\}").search("${FOO}")
    assert _expand_env_var(match) == "bar"


def test_expand_env_var_default_dash(monkeypatch):
    monkeypatch.delenv("MISSING", raising=False)
    import re
    match = re.compile(r"\$\{([^}]+)\}").search("${MISSING:-default}")
    assert _expand_env_var(match) == "default"


def test_expand_env_var_default_colon(monkeypatch):
    monkeypatch.delenv("MISSING", raising=False)
    import re
    match = re.compile(r"\$\{([^}]+)\}").search("${MISSING:default}")
    assert _expand_env_var(match) == "default"


def test_expand_env_var_present_takes_precedence_over_default(monkeypatch):
    monkeypatch.setenv("FOO", "bar")
    import re
    match = re.compile(r"\$\{([^}]+)\}").search("${FOO:-default}")
    assert _expand_env_var(match) == "bar"


def test_expand_env_vars_type_coercion_int(monkeypatch):
    monkeypatch.setenv("PREFETCH", "5")
    result = _expand_env_vars({"prefetch": "${PREFETCH}"})
    assert result["prefetch"] == 5
    assert isinstance(result["prefetch"], int)


def test_expand_env_vars_type_coercion_bool(monkeypatch):
    monkeypatch.setenv("ENABLED", "true")
    result = _expand_env_vars({"enabled": "${ENABLED}"})
    assert result["enabled"] is True
    assert isinstance(result["enabled"], bool)


def test_expand_env_vars_type_coercion_float(monkeypatch):
    monkeypatch.setenv("RATIO", "3.14")
    result = _expand_env_vars({"ratio": "${RATIO}"})
    assert result["ratio"] == 3.14
    assert isinstance(result["ratio"], float)


def test_expand_env_vars_type_coercion_dict(monkeypatch):
    monkeypatch.setenv("ENGINE_OPTIONS", '{"connect_args": {"timeout": 5}}')
    result = _expand_env_vars({"engine_options": "${ENGINE_OPTIONS}"})
    assert result["engine_options"] == {"connect_args": {"timeout": 5}}
    assert isinstance(result["engine_options"], dict)


def test_expand_env_vars_type_coercion_list(monkeypatch):
    monkeypatch.setenv("ITEMS", "[1, 2, 3]")
    result = _expand_env_vars({"items": "${ITEMS}"})
    assert result["items"] == [1, 2, 3]
    assert isinstance(result["items"], list)


def test_expand_env_vars_type_coercion_null(monkeypatch):
    monkeypatch.setenv("NOT_SET", "null")
    result = _expand_env_vars({"value": "${NOT_SET}"})
    # null parsed to None → returns original string
    assert result["value"] == "null"


def test_expand_env_vars_type_coercion_empty_string(monkeypatch):
    monkeypatch.setenv("EMPTY", "")
    result = _expand_env_vars({"value": "${EMPTY}"})
    assert result["value"] == ""


def test_expand_env_vars_json_string_forces_string_type(monkeypatch):
    monkeypatch.setenv("VALUE", '"123"')

    result = _expand_env_vars({"value": "${VALUE}"})

    assert result["value"] == "123"
    assert isinstance(result["value"], str)


@pytest.mark.parametrize(
    "value",
    [
        "yes",
        "no",
        "on",
        "off",
        "2026-08-03",
        "0123",
        "00123",
        "NaN",
        "Infinity",
        "-Infinity",
        '{"threshold": NaN}',
    ],
)
def test_expand_env_vars_preserves_non_json_yaml_scalars(monkeypatch, value):
    monkeypatch.setenv("VALUE", value)

    result = _expand_env_vars({"value": "${VALUE}"})

    assert result["value"] == value
    assert isinstance(result["value"], str)


def test_expand_env_vars_type_coercion_default_with_type(monkeypatch):
    monkeypatch.delenv("PREFETCH", raising=False)
    result = _expand_env_vars({"prefetch": "${PREFETCH:-5}"})
    assert result["prefetch"] == 5
    assert isinstance(result["prefetch"], int)


def test_expand_env_vars_type_coercion_default_colon_syntax(monkeypatch):
    monkeypatch.delenv("PREFETCH", raising=False)
    result = _expand_env_vars({"prefetch": "${PREFETCH:5}"})
    assert result["prefetch"] == 5
    assert isinstance(result["prefetch"], int)


def test_expand_env_vars_type_coercion_default_bool(monkeypatch):
    monkeypatch.delenv("ENABLED", raising=False)
    result = _expand_env_vars({"enabled": "${ENABLED:-true}"})
    assert result["enabled"] is True
    assert isinstance(result["enabled"], bool)


def test_expand_env_vars_type_coercion_default_float(monkeypatch):
    monkeypatch.delenv("RATIO", raising=False)
    result = _expand_env_vars({"ratio": "${RATIO:3.14}"})
    assert result["ratio"] == 3.14
    assert isinstance(result["ratio"], float)


def test_expand_env_vars_type_coercion_default_string(monkeypatch):
    monkeypatch.delenv("NAME", raising=False)
    result = _expand_env_vars({"name": "${NAME:-hello}"})
    assert result["name"] == "hello"
    assert isinstance(result["name"], str)


def test_expand_env_vars_mixed_string_preserved(monkeypatch):
    monkeypatch.setenv("HOST", "localhost")
    # Mixed string with prefix/suffix should stay as string
    result = _expand_env_vars({"dsn": "mysql://${HOST}:3306/mydb"})
    assert result["dsn"] == "mysql://localhost:3306/mydb"
    assert isinstance(result["dsn"], str)


def test_expand_env_vars_multiple_references_preserved(monkeypatch):
    monkeypatch.setenv("MAJOR", "1")
    monkeypatch.setenv("MINOR", "2")

    result = _expand_env_vars({"version": "${MAJOR}${MINOR}"})

    assert result["version"] == "12"
    assert isinstance(result["version"], str)


def test_expand_env_vars_non_string_values_unchanged(monkeypatch):
    result = _expand_env_vars({"count": 5, "enabled": True, "ratio": 3.14})
    assert result["count"] == 5
    assert result["enabled"] is True
    assert result["ratio"] == 3.14


def test_expand_env_vars_recursive(monkeypatch):
    monkeypatch.setenv("A", "1")
    monkeypatch.setenv("B", "2")
    config = {
        "app": {"name": "${A}"},
        "tasks": [{"config": {"key": "${B}"}}],
    }
    result = _expand_env_vars(config)
    # Pure ${VAR} references decode JSON literals: "1" -> 1 (int).
    assert result["app"]["name"] == 1
    assert isinstance(result["app"]["name"], int)
    assert result["tasks"][0]["config"]["key"] == 2
    assert isinstance(result["tasks"][0]["config"]["key"], int)


# ---------------------------------------------------------------------------
# load_yaml_app with env_file
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("app_name", ["yes", "off", "2026-08-03", "0123"])
def test_load_yaml_app_preserves_string_env_name(tmp_path, monkeypatch, app_name):
    pytest.importorskip("yaml")
    monkeypatch.setenv("APP_NAME", app_name)
    config_path = tmp_path / "app.yaml"
    config_path.write_text("name: ${APP_NAME}\ntasks: []\n", encoding="utf-8")

    app = load_yaml_app(str(config_path))

    assert app.name == app_name


def test_load_yaml_app_with_env_file(tmp_path, monkeypatch):
    env_file = tmp_path / "test.env"
    env_file.write_text("DB_DSN=mysql://localhost\nPOOL_SIZE=20\n", encoding="utf-8")
    config_path = tmp_path / "app.yaml"
    config_path.write_text(
        json.dumps(
            {
                "name": "env-test",
                "resources": {
                    "incoming": {"type": "memory"},
                },
                "tasks": [
                    {
                        "name": "consume",
                        "source": "incoming",
                        "handler": "testsupport_config_env:consume",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    async def consume(ctx, item):
        return item

    with _registered_yaml_module(), _registered_module("testsupport_config_env", consume=consume):
        app = load_yaml_app(str(config_path), env_file=str(env_file))
    assert os.environ["DB_DSN"] == "mysql://localhost"
    assert os.environ["POOL_SIZE"] == "20"
    assert app.name == "env-test"


def test_load_yaml_app_env_file_not_found(tmp_path):
    config_path = tmp_path / "app.yaml"
    config_path.write_text(
        json.dumps({"name": "no-env", "tasks": []}),
        encoding="utf-8",
    )
    with _registered_yaml_module():
        with pytest.raises(ValueError, match="env_file not found"):
            load_yaml_app(str(config_path), env_file="/nonexistent/path.env")


def test_load_yaml_app_auto_detect_env_file(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("AUTO_VAR=detected\n", encoding="utf-8")
    config_path = tmp_path / "app.yaml"
    config_path.write_text(
        json.dumps({"name": "auto-env", "tasks": []}),
        encoding="utf-8",
    )
    with _registered_yaml_module():
        app = load_yaml_app(str(config_path))
    assert os.environ["AUTO_VAR"] == "detected"
    assert app.name == "auto-env"


def test_load_yaml_app_no_dotenv_silent(tmp_path):
    config_path = tmp_path / "app.yaml"
    config_path.write_text(
        json.dumps({"name": "no-dotenv", "tasks": []}),
        encoding="utf-8",
    )
    # No .env file present — should not raise
    with _registered_yaml_module():
        app = load_yaml_app(str(config_path))
    assert app.name == "no-dotenv"


def test_load_yaml_app_env_file_priority_cli_over_yaml(tmp_path, monkeypatch):
    cli_env = tmp_path / "cli.env"
    cli_env.write_text("PRIORITY=from-cli\n", encoding="utf-8")
    yaml_env = tmp_path / "yaml.env"
    yaml_env.write_text("PRIORITY=from-yaml\n", encoding="utf-8")
    config_path = tmp_path / "app.yaml"
    config_path.write_text(
        json.dumps(
            {
                "app": {"name": "priority-test", "env_file": "yaml.env"},
                "tasks": [],
            }
        ),
        encoding="utf-8",
    )
    with _registered_yaml_module():
        load_yaml_app(str(config_path), env_file=str(cli_env))
    assert os.environ["PRIORITY"] == "from-cli"


def test_load_yaml_app_env_file_relative_to_yaml(tmp_path):
    sub_dir = tmp_path / "config"
    sub_dir.mkdir()
    env_file = sub_dir / "app.env"
    env_file.write_text("REL_VAR=relative\n", encoding="utf-8")
    config_path = sub_dir / "app.yaml"
    config_path.write_text(
        json.dumps(
            {
                "app": {"name": "relative-test", "env_file": "app.env"},
                "tasks": [],
            }
        ),
        encoding="utf-8",
    )
    with _registered_yaml_module():
        load_yaml_app(str(config_path))
    assert os.environ["REL_VAR"] == "relative"


# ---------------------------------------------------------------------------
# load_yaml_app with strict_env
# ---------------------------------------------------------------------------

def test_load_yaml_app_strict_env_missing_var(tmp_path, monkeypatch):
    monkeypatch.delenv("MISSING_SECRET", raising=False)
    config_path = tmp_path / "strict-app.yaml"
    config_path.write_text(
        json.dumps(
            {
                "name": "strict-test",
                "resources": {
                    "api": {
                        "type": "memory",
                        "token": "${MISSING_SECRET}",
                    },
                },
                "tasks": [],
            }
        ),
        encoding="utf-8",
    )
    with _registered_yaml_module():
        with pytest.raises(ValueError, match="strict_env: missing required environment variable"):
            load_yaml_app(str(config_path), strict_env=True)


def test_load_yaml_app_strict_env_all_present(tmp_path, monkeypatch):
    monkeypatch.setenv("API_TOKEN", "present")
    config_path = tmp_path / "strict-ok.yaml"
    config_path.write_text(
        json.dumps(
            {
                "name": "strict-ok",
                "resources": {
                    "api": {
                        "type": "memory",
                        "token": "${API_TOKEN}",
                    },
                },
                "tasks": [],
            }
        ),
        encoding="utf-8",
    )
    with _registered_yaml_module():
        app = load_yaml_app(str(config_path), strict_env=True)
    assert app.name == "strict-ok"


def test_load_yaml_app_strict_env_with_defaults_ok(tmp_path, monkeypatch):
    monkeypatch.delenv("TIMEOUT", raising=False)
    config_path = tmp_path / "defaults.yaml"
    config_path.write_text(
        json.dumps(
            {
                "name": "defaults-test",
                "resources": {
                    "api": {
                        "type": "memory",
                        "timeout": "${TIMEOUT:-30}",
                    },
                },
                "tasks": [],
            }
        ),
        encoding="utf-8",
    )
    with _registered_yaml_module():
        app = load_yaml_app(str(config_path), strict_env=True)
    assert app.name == "defaults-test"


def test_load_yaml_app_strict_env_from_app_field(tmp_path, monkeypatch):
    monkeypatch.delenv("DB_DSN", raising=False)
    config_path = tmp_path / "app-strict.yaml"
    config_path.write_text(
        json.dumps(
            {
                "app": {"name": "app-strict-test", "strict_env": True},
                "resources": {
                    "db": {
                        "type": "memory",
                        "dsn": "${DB_DSN}",
                    },
                },
                "tasks": [],
            }
        ),
        encoding="utf-8",
    )
    with _registered_yaml_module():
        with pytest.raises(ValueError, match="strict_env: missing required environment variable"):
            load_yaml_app(str(config_path))


def test_load_yaml_app_strict_env_cli_overrides_app_field(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_DSN", "from-shell")
    config_path = tmp_path / "override-strict.yaml"
    config_path.write_text(
        json.dumps(
            {
                "app": {"name": "override-test", "strict_env": False},
                "resources": {
                    "db": {
                        "type": "memory",
                        "dsn": "${DB_DSN}",
                        "extra": "${MISSING}",
                    },
                },
                "tasks": [],
            }
        ),
        encoding="utf-8",
    )
    with _registered_yaml_module():
        # CLI strict_env=True should override app.strict_env=False
        with pytest.raises(ValueError, match="strict_env: missing required environment variable"):
            load_yaml_app(str(config_path), strict_env=True)


def test_load_yaml_app_strict_env_error_message_includes_paths(tmp_path, monkeypatch):
    monkeypatch.delenv("DB_PASSWORD", raising=False)
    monkeypatch.delenv("API_TOKEN", raising=False)
    config_path = tmp_path / "multi.yaml"
    config_path.write_text(
        json.dumps(
            {
                "name": "multi-error",
                "resources": {
                    "db": {"type": "memory", "dsn": "${DB_PASSWORD}"},
                    "api": {"type": "memory", "token": "${API_TOKEN}"},
                },
                "tasks": [],
            }
        ),
        encoding="utf-8",
    )
    with _registered_yaml_module():
        with pytest.raises(ValueError) as exc_info:
            load_yaml_app(str(config_path), strict_env=True)
        msg = str(exc_info.value)
        assert "API_TOKEN" in msg
        assert "DB_PASSWORD" in msg
        assert "config.resources.db.dsn" in msg
        assert "config.resources.api.token" in msg
        assert "Hint" in msg


# ---------------------------------------------------------------------------
# strict validation for env_file / strict_env types
# ---------------------------------------------------------------------------

def test_validate_app_config_rejects_non_string_env_file():
    from onestep.config import validate_app_config
    with pytest.raises(TypeError, match="'app.env_file' must be a string"):
        validate_app_config(
            {
                "apiVersion": "onestep/v1alpha1",
                "kind": "App",
                "app": {"name": "test", "env_file": 123},
                "tasks": [],
            }
        )


def test_validate_app_config_rejects_non_bool_strict_env():
    from onestep.config import validate_app_config
    with pytest.raises(TypeError, match="'app.strict_env' must be a boolean"):
        validate_app_config(
            {
                "apiVersion": "onestep/v1alpha1",
                "kind": "App",
                "app": {"name": "test", "strict_env": 1},
                "tasks": [],
            }
        )


# ---------------------------------------------------------------------------
# CLI: --env-file and --strict-env
# ---------------------------------------------------------------------------

def test_cli_check_with_env_file(tmp_path, capsys):
    env_file = tmp_path / "test.env"
    env_file.write_text("CLI_ENV_VAR=from-cli\n", encoding="utf-8")
    config_path = tmp_path / "app.yaml"
    config_path.write_text(
        json.dumps({"name": "cli-env-test", "tasks": []}),
        encoding="utf-8",
    )
    with _registered_yaml_module():
        exit_code = main(["check", "--env-file", str(env_file), str(config_path)])
    assert exit_code == 0
    assert os.environ["CLI_ENV_VAR"] == "from-cli"


def test_cli_check_with_strict_env_missing_var(tmp_path, capsys):
    config_path = tmp_path / "strict-cli.yaml"
    config_path.write_text(
        json.dumps(
            {
                "name": "strict-cli-test",
                "resources": {
                    "db": {"type": "memory", "dsn": "${MISSING_VAR}"},
                },
                "tasks": [],
            }
        ),
        encoding="utf-8",
    )
    with _registered_yaml_module():
        exit_code = main(["check", "--strict-env", str(config_path)])
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "strict_env: missing required environment variable" in captured.err


def test_cli_check_honors_app_strict_env_when_flag_omitted(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("APP_STRICT_MISSING_VAR", raising=False)
    config_path = tmp_path / "app-strict-cli.yaml"
    config_path.write_text(
        json.dumps(
            {
                "app": {"name": "app-strict-cli-test", "strict_env": True},
                "resources": {
                    "db": {"type": "memory", "dsn": "${APP_STRICT_MISSING_VAR}"},
                },
                "tasks": [],
            }
        ),
        encoding="utf-8",
    )
    with _registered_yaml_module():
        exit_code = main(["check", str(config_path)])
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "APP_STRICT_MISSING_VAR" in captured.err


def test_cli_check_with_strict_env_and_env_file(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("FROM_ENV_FILE", raising=False)
    env_file = tmp_path / "prod.env"
    env_file.write_text("FROM_ENV_FILE=loaded\n", encoding="utf-8")
    config_path = tmp_path / "app.yaml"
    config_path.write_text(
        json.dumps(
            {
                "name": "combined-test",
                "resources": {
                    "db": {"type": "memory", "dsn": "${FROM_ENV_FILE}"},
                },
                "tasks": [],
            }
        ),
        encoding="utf-8",
    )
    with _registered_yaml_module():
        exit_code = main(
            ["check", "--env-file", str(env_file), "--strict-env", str(config_path)]
        )
    assert exit_code == 0
    assert os.environ["FROM_ENV_FILE"] == "loaded"


def test_cli_run_with_env_file(tmp_path, capsys, monkeypatch):
    env_file = tmp_path / "run.env"
    env_file.write_text("RUN_VAR=loaded\n", encoding="utf-8")
    config_path = tmp_path / "app.yaml"
    config_path.write_text(
        json.dumps(
            {
                "name": "cli-run-env",
                "tasks": [],
            }
        ),
        encoding="utf-8",
    )
    # Use run command to verify --env-file works on run too
    with _registered_yaml_module():
        exit_code = main(["run", "--env-file", str(env_file), str(config_path)])
    assert exit_code == 0
    assert os.environ["RUN_VAR"] == "loaded"


def test_load_yaml_app_strict_env_type_error(tmp_path):
    config_path = tmp_path / "bad-strict.yaml"
    config_path.write_text(
        json.dumps(
            {
                "app": {"name": "bad-strict", "strict_env": "yes"},
                "tasks": [],
            }
        ),
        encoding="utf-8",
    )
    with _registered_yaml_module():
        with pytest.raises(TypeError, match="'app.strict_env' must be a boolean"):
            load_yaml_app(str(config_path))


def test_load_yaml_app_env_file_type_error(tmp_path):
    config_path = tmp_path / "bad-env.yaml"
    config_path.write_text(
        json.dumps(
            {
                "app": {"name": "bad-env", "env_file": 42},
                "tasks": [],
            }
        ),
        encoding="utf-8",
    )
    with _registered_yaml_module():
        with pytest.raises(TypeError, match="'app.env_file' must be a string"):
            load_yaml_app(str(config_path))


# ---------------------------------------------------------------------------
# _make_expand_env_var
# ---------------------------------------------------------------------------

def test_make_expand_env_var_uses_env_over_os_environ(monkeypatch):
    monkeypatch.setenv("FOO", "from-shell")
    expand = _make_expand_env_var({"FOO": "from-env"})
    import re
    match = re.compile(r"\$\{([^}]+)\}").search("${FOO}")
    assert expand(match) == "from-env"


def test_make_expand_env_var_falls_back_to_os_environ(monkeypatch):
    monkeypatch.setenv("FOO", "from-shell")
    expand = _make_expand_env_var({"OTHER": "other"})
    import re
    match = re.compile(r"\$\{([^}]+)\}").search("${FOO}")
    assert expand(match) == "from-shell"


def test_make_expand_env_var_default_with_env(monkeypatch):
    monkeypatch.delenv("MISSING", raising=False)
    expand = _make_expand_env_var({"MISSING": "from-env"})
    import re
    match = re.compile(r"\$\{([^}]+)\}").search("${MISSING:-default}")
    assert expand(match) == "from-env"


def test_make_expand_env_var_default_fallback(monkeypatch):
    monkeypatch.delenv("MISSING", raising=False)
    expand = _make_expand_env_var({})
    import re
    match = re.compile(r"\$\{([^}]+)\}").search("${MISSING:-default}")
    assert expand(match) == "default"


def test_make_expand_env_var_none_env_uses_os_environ(monkeypatch):
    monkeypatch.setenv("FOO", "from-shell")
    expand = _make_expand_env_var(None)
    import re
    match = re.compile(r"\$\{([^}]+)\}").search("${FOO}")
    assert expand(match) == "from-shell"


# ---------------------------------------------------------------------------
# _expand_env_vars with env
# ---------------------------------------------------------------------------

def test_expand_env_vars_env_overrides_os_environ(monkeypatch):
    monkeypatch.setenv("PREFETCH", "100")
    result = _expand_env_vars({"prefetch": "${PREFETCH}"}, env={"PREFETCH": "5"})
    assert result["prefetch"] == 5


def test_expand_env_vars_env_falls_back_to_os_environ(monkeypatch):
    monkeypatch.setenv("PREFETCH", "5")
    result = _expand_env_vars({"prefetch": "${PREFETCH}"}, env={"OTHER": "x"})
    assert result["prefetch"] == 5


def test_expand_env_vars_env_mixed_string(monkeypatch):
    monkeypatch.setenv("HOST", "from-shell")
    result = _expand_env_vars(
        {"dsn": "mysql://${HOST}:3306/db"},
        env={"HOST": "from-env"},
    )
    assert result["dsn"] == "mysql://from-env:3306/db"


# ---------------------------------------------------------------------------
# _collect_env_refs with env
# ---------------------------------------------------------------------------

def test_collect_env_refs_env_satisfies_variable(monkeypatch):
    monkeypatch.delenv("SECRET", raising=False)
    config = {"resources": {"db": {"dsn": "${SECRET}"}}}
    missing = _collect_env_refs(config, env={"SECRET": "present"})
    assert "SECRET" not in missing


def test_collect_env_refs_env_missing_both(monkeypatch):
    monkeypatch.delenv("SECRET", raising=False)
    config = {"resources": {"db": {"dsn": "${SECRET}"}}}
    missing = _collect_env_refs(config, env={"OTHER": "x"})
    assert "SECRET" in missing


# ---------------------------------------------------------------------------
# load_yaml_app with env
# ---------------------------------------------------------------------------

def test_load_yaml_app_with_env_param(tmp_path, monkeypatch):
    monkeypatch.delenv("MY_DSN", raising=False)
    config_path = tmp_path / "app.yaml"
    config_path.write_text(
        json.dumps(
            {
                "name": "env-param-test",
                "resources": {
                    "db": {"type": "memory", "dsn": "${MY_DSN}"},
                },
                "tasks": [],
            }
        ),
        encoding="utf-8",
    )
    with _registered_yaml_module():
        app = load_yaml_app(str(config_path), env={"MY_DSN": "db://localhost"})
    assert app.name == "env-param-test"


def test_load_yaml_app_env_param_overrides_os_environ(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_DSN", "from-shell")
    config_path = tmp_path / "app.yaml"
    config_path.write_text(
        json.dumps(
            {
                "name": "override-test",
                "resources": {
                    "db": {"type": "memory", "dsn": "${MY_DSN}"},
                },
                "tasks": [],
            }
        ),
        encoding="utf-8",
    )
    with _registered_yaml_module():
        app = load_yaml_app(str(config_path), env={"MY_DSN": "from-env-param"})
    assert app.name == "override-test"


def test_load_yaml_app_strict_env_with_env_param(tmp_path, monkeypatch):
    monkeypatch.delenv("SECRET", raising=False)
    config_path = tmp_path / "app.yaml"
    config_path.write_text(
        json.dumps(
            {
                "name": "strict-env-param",
                "resources": {
                    "db": {"type": "memory", "dsn": "${SECRET}"},
                },
                "tasks": [],
            }
        ),
        encoding="utf-8",
    )
    with _registered_yaml_module():
        app = load_yaml_app(
            str(config_path),
            strict_env=True,
            env={"SECRET": "from-env-param"},
        )
    assert app.name == "strict-env-param"
