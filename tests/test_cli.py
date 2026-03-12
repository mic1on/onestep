import asyncio
import json
import os
import sys
import types
from contextlib import contextmanager

import onestep.config as config_module
from onestep import MemoryQueue, OneStepApp
from onestep.cli import main
from onestep.connectors.base import Sink, Source


@contextmanager
def registered_module(name: str, **attrs):
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


@contextmanager
def registered_yaml_module():
    def safe_load(handle):
        return json.loads(handle.read())

    with registered_module("yaml", safe_load=safe_load) as module:
        yield module


def clear_modules(monkeypatch, *names: str) -> None:
    for name in names:
        monkeypatch.delitem(sys.modules, name, raising=False)


def test_cli_check_prints_task_summary(capsys) -> None:
    source = MemoryQueue("incoming")
    sink = MemoryQueue("processed")
    app = OneStepApp("cli-check")

    @app.task(
        source=source,
        emit=sink,
        timeout_s=5.0,
        description="Process incoming jobs and forward them to the processed queue.",
    )
    async def consume(ctx, item):
        return item

    with registered_module("testsupport_cli_check", app=app):
        exit_code = main(["check", "testsupport_cli_check:app"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Target: testsupport_cli_check:app" in captured.out
    assert "App: cli-check" in captured.out
    assert "- consume source=incoming<MemoryQueue> emit=processed<MemoryQueue>" in captured.out
    assert "timeout=5.00s" in captured.out
    assert "description='Process incoming jobs and forward them to the processed queue.'" in captured.out


def test_cli_check_json_supports_factory_targets(capsys) -> None:
    def build_app() -> OneStepApp:
        app = OneStepApp("cli-factory")
        source = MemoryQueue("factory.incoming")

        @app.task(source=source)
        async def consume(ctx, item):
            """Consume a single item from the factory queue."""
            return None

        return app

    with registered_module("testsupport_cli_factory", build_app=build_app):
        exit_code = main(["check", "--json", "testsupport_cli_factory:build_app"])

    captured = capsys.readouterr()
    summary = json.loads(captured.out)
    assert exit_code == 0
    assert summary["target"] == "testsupport_cli_factory:build_app"
    assert summary["name"] == "cli-factory"
    assert summary["tasks"][0]["name"] == "consume"
    assert summary["tasks"][0]["description"] == "Consume a single item from the factory queue."
    assert summary["tasks"][0]["source"] == {"name": "factory.incoming", "type": "MemoryQueue"}


def test_cli_run_shorthand_executes_target(capsys) -> None:
    app = OneStepApp("cli-run")

    @app.on_startup
    def started() -> None:
        print("cli target started")

    with registered_module("testsupport_cli_run", app=app):
        exit_code = main(["testsupport_cli_run:app"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "cli target started" in captured.out


def test_cli_invalid_target_returns_non_zero(capsys) -> None:
    exit_code = main(["check", "testsupport_missing_module:app"])
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "failed to load testsupport_missing_module:app" in captured.err


def test_cli_check_loads_modules_from_current_working_directory(tmp_path, monkeypatch, capsys) -> None:
    example_dir = tmp_path / "example"
    example_dir.mkdir()
    (example_dir / "cli_app.py").write_text(
        "\n".join(
            [
                "from onestep import OneStepApp",
                "",
                "app = OneStepApp('cwd-cli-app')",
                "",
                "@app.task()",
                "async def consume(ctx, item):",
                "    return None",
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        sys,
        "path",
        [entry for entry in sys.path if os.path.abspath(entry) != os.path.abspath(str(tmp_path))],
    )
    clear_modules(monkeypatch, "example", "example.cli_app")

    exit_code = main(["check", "example.cli_app:app"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "App: cwd-cli-app" in captured.out


def test_cli_check_loads_modules_from_src_layout(tmp_path, monkeypatch, capsys) -> None:
    src_dir = tmp_path / "src" / "example"
    src_dir.mkdir(parents=True)
    (src_dir / "__init__.py").write_text("", encoding="utf-8")
    (src_dir / "cli_app.py").write_text(
        "\n".join(
            [
                "from onestep import OneStepApp",
                "",
                "app = OneStepApp('src-cli-app')",
                "",
                "@app.task()",
                "async def consume(ctx, item):",
                "    return None",
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        sys,
        "path",
        [
            entry
            for entry in sys.path
            if os.path.abspath(entry) not in {os.path.abspath(str(tmp_path)), os.path.abspath(str(tmp_path / 'src'))}
        ],
    )
    clear_modules(monkeypatch, "example", "example.cli_app")

    exit_code = main(["check", "example.cli_app:app"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "App: src-cli-app" in captured.out


def test_cli_check_loads_modules_from_project_root_when_running_in_subdirectory(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'test-app'\nversion = '0.0.0'\n", encoding="utf-8")
    src_dir = tmp_path / "src" / "example"
    src_dir.mkdir(parents=True)
    (src_dir / "__init__.py").write_text("", encoding="utf-8")
    (src_dir / "cli_app.py").write_text(
        "\n".join(
            [
                "from onestep import OneStepApp",
                "",
                "app = OneStepApp('project-root-cli-app')",
                "",
                "@app.task()",
                "async def consume(ctx, item):",
                "    return None",
                "",
            ]
        ),
        encoding="utf-8",
    )
    work_dir = tmp_path / "tools"
    work_dir.mkdir()
    monkeypatch.chdir(work_dir)
    monkeypatch.setattr(
        sys,
        "path",
        [
            entry
            for entry in sys.path
            if os.path.abspath(entry)
            not in {
                os.path.abspath(str(work_dir)),
                os.path.abspath(str(tmp_path)),
                os.path.abspath(str(tmp_path / "src")),
            }
        ],
    )
    clear_modules(monkeypatch, "example", "example.cli_app")

    exit_code = main(["check", "example.cli_app:app"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "App: project-root-cli-app" in captured.out


def test_cli_check_loads_yaml_target(capsys, tmp_path) -> None:
    config_path = tmp_path / "app.yaml"
    config_path.write_text(
        json.dumps(
            {
                "name": "yaml-cli-app",
                "connectors": {
                    "incoming": {"type": "memory"},
                    "processed": {"type": "memory"},
                },
                "tasks": [
                    {
                        "name": "consume",
                        "description": "Consume queued messages from YAML config.",
                        "source": "incoming",
                        "handler": "testsupport_yaml_cli:consume",
                        "emit": ["processed"],
                        "retry": {"type": "max_attempts", "max_attempts": 2, "delay_s": 0},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    async def consume(ctx, item):
        return item

    with registered_yaml_module(), registered_module("testsupport_yaml_cli", consume=consume):
        exit_code = main(["check", str(config_path)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert f"Target: {config_path}" in captured.out
    assert "App: yaml-cli-app" in captured.out
    assert "- consume source=incoming<MemoryQueue> emit=processed<MemoryQueue>" in captured.out
    assert "retry=MaxAttempts" in captured.out
    assert "description='Consume queued messages from YAML config.'" in captured.out


def test_yaml_target_reuses_connector_instances_and_binds_handler_params(tmp_path) -> None:
    config_path = tmp_path / "pipeline.yaml"
    config_path.write_text(
        json.dumps(
            {
                "name": "yaml-pipeline",
                "connectors": {
                    "incoming": {"type": "memory"},
                    "processed": {"type": "memory"},
                },
                "tasks": [
                    {
                        "name": "double",
                        "source": "incoming",
                        "handler": {
                            "ref": "testsupport_yaml_runtime:double",
                            "params": {"factor": 2},
                        },
                        "emit": "processed",
                    },
                    {
                        "name": "collect",
                        "source": "processed",
                        "handler": "testsupport_yaml_runtime:collect",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    seen: list[dict[str, int]] = []

    async def double(ctx, item, *, factor: int):
        return {"value": item["value"] * factor}

    async def collect(ctx, item):
        seen.append(item)
        ctx.app.request_shutdown()

    async def scenario() -> None:
        with registered_yaml_module(), registered_module(
            "testsupport_yaml_runtime",
            double=double,
            collect=collect,
        ):
            app = OneStepApp.load(str(config_path))
            assert app.tasks[0].sinks[0] is app.tasks[1].source
            await app.tasks[0].source.publish({"value": 21})
            await app.serve()

    asyncio.run(scenario())
    assert seen == [{"value": 42}]


def test_yaml_target_builds_mysql_and_rabbitmq_resources_via_refs(monkeypatch, tmp_path) -> None:
    class FakeCursorStore:
        def __init__(self, *, connector, kwargs):
            self.connector = connector
            self.kwargs = kwargs

        async def load(self, key: str):
            return None

        async def save(self, key: str, value):
            return None

    class FakeSource(Source):
        def __init__(self, name: str, *, connector, kind: str, kwargs):
            super().__init__(name)
            self.connector = connector
            self.kind = kind
            self.kwargs = kwargs

        async def fetch(self, limit: int):
            return []

    class FakeQueue(FakeSource, Sink):
        def __init__(self, name: str, *, connector, kind: str, kwargs):
            FakeSource.__init__(self, name, connector=connector, kind=kind, kwargs=kwargs)
            Sink.__init__(self, name)

        async def send(self, envelope):
            return None

    class FakeSink(Sink):
        def __init__(self, name: str, *, connector, kind: str, kwargs):
            super().__init__(name)
            self.connector = connector
            self.kind = kind
            self.kwargs = kwargs

        async def send(self, envelope):
            return None

    class FakeRabbitMQConnector:
        def __init__(self, url: str, options=None):
            self.url = url
            self.options = options

        def queue(self, name: str, **kwargs):
            return FakeQueue(name, connector=self, kind="rabbitmq_queue", kwargs=kwargs)

    class FakeMySQLConnector:
        def __init__(self, dsn: str, **engine_options):
            self.dsn = dsn
            self.engine_options = engine_options
            self.cursor_stores = []

        def cursor_store(self, **kwargs):
            store = FakeCursorStore(connector=self, kwargs=kwargs)
            self.cursor_stores.append(store)
            return store

        def incremental(self, **kwargs):
            return FakeSource(f"mysql.incremental:{kwargs['table']}", connector=self, kind="mysql_incremental", kwargs=kwargs)

        def table_sink(self, **kwargs):
            return FakeSink(f"mysql.table_sink:{kwargs['table']}", connector=self, kind="mysql_table_sink", kwargs=kwargs)

    monkeypatch.setattr(config_module, "RabbitMQConnector", FakeRabbitMQConnector)
    monkeypatch.setattr(config_module, "MySQLConnector", FakeMySQLConnector)

    config_path = tmp_path / "advanced.yaml"
    config_path.write_text(
        json.dumps(
            {
                "name": "yaml-advanced",
                "connectors": {
                    "rmq": {
                        "type": "rabbitmq",
                        "url": "amqp://guest:guest@localhost/",
                        "options": {"client_properties": {"connection_name": "yaml-worker"}},
                    },
                    "jobs_in": {
                        "type": "rabbitmq_queue",
                        "connector": "rmq",
                        "queue": "incoming_jobs",
                        "exchange": "jobs.events",
                        "routing_key": "jobs.created",
                        "prefetch": 50,
                    },
                    "db": {
                        "type": "mysql",
                        "dsn": "mysql+pymysql://root:root@localhost:3306/app",
                        "engine_options": {"pool_recycle": 3600},
                    },
                    "cursor": {
                        "type": "mysql_cursor_store",
                        "connector": "db",
                        "table": "onestep_cursor",
                    },
                    "users": {
                        "type": "mysql_incremental",
                        "connector": "db",
                        "table": "users",
                        "key": "id",
                        "cursor": ["updated_at", "id"],
                        "where": "deleted = 0",
                        "state": "cursor",
                        "state_key": "users-sync",
                    },
                    "processed": {
                        "type": "mysql_table_sink",
                        "connector": "db",
                        "table": "processed_users",
                        "mode": "upsert",
                        "keys": ["id"],
                    },
                },
                "tasks": [
                    {
                        "name": "sync_users",
                        "source": "users",
                        "handler": "testsupport_yaml_advanced:sync_users",
                        "emit": ["processed"],
                    },
                    {
                        "name": "ingest_jobs",
                        "source": "jobs_in",
                        "handler": "testsupport_yaml_advanced:ingest_jobs",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    async def sync_users(ctx, item):
        return item

    async def ingest_jobs(ctx, item):
        return None

    with registered_yaml_module(), registered_module(
        "testsupport_yaml_advanced",
        sync_users=sync_users,
        ingest_jobs=ingest_jobs,
    ):
        app = OneStepApp.load(str(config_path))

    sync_task = app.tasks[0]
    ingest_task = app.tasks[1]

    assert sync_task.source.kind == "mysql_incremental"
    assert sync_task.source.kwargs["cursor"] == ("updated_at", "id")
    assert sync_task.source.kwargs["state"] is sync_task.source.connector.cursor_stores[0]
    assert sync_task.source.kwargs["state_key"] == "users-sync"
    assert sync_task.source.connector.engine_options == {"pool_recycle": 3600}
    assert sync_task.sinks[0].kind == "mysql_table_sink"
    assert sync_task.sinks[0].connector is sync_task.source.connector
    assert ingest_task.source.kind == "rabbitmq_queue"
    assert ingest_task.source.name == "incoming_jobs"
    assert ingest_task.source.connector.url == "amqp://guest:guest@localhost/"
    assert ingest_task.source.connector.options == {"client_properties": {"connection_name": "yaml-worker"}}
    assert ingest_task.source.kwargs["exchange"] == "jobs.events"
    assert ingest_task.source.kwargs["prefetch"] == 50


def test_yaml_target_builds_webhook_auth_and_response(tmp_path) -> None:
    config_path = tmp_path / "webhook.yaml"
    config_path.write_text(
        json.dumps(
            {
                "name": "yaml-webhook",
                "connectors": {
                    "incoming": {
                        "type": "webhook",
                        "path": "/hooks/protected",
                        "methods": ["POST", "PUT"],
                        "auth": {
                            "type": "bearer",
                            "token": "secret-token",
                            "realm": "yaml",
                        },
                        "response": {
                            "status_code": 200,
                            "body": {"accepted": True},
                            "headers": {"X-Test": "1"},
                        },
                    },
                    "out": {"type": "memory"},
                },
                "tasks": [
                    {
                        "name": "ingest",
                        "source": "incoming",
                        "handler": "testsupport_yaml_webhook:ingest",
                        "emit": ["out"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    async def ingest(ctx, item):
        return item

    with registered_yaml_module(), registered_module("testsupport_yaml_webhook", ingest=ingest):
        app = OneStepApp.load(str(config_path))

    source = app.tasks[0].source
    status_code, headers, payload = source.response.render()

    assert source.path == "/hooks/protected"
    assert source.methods == ("POST", "PUT")
    assert source.auth.token == "secret-token"
    assert source.auth.challenge_headers()["WWW-Authenticate"] == 'Bearer realm="yaml"'
    assert status_code == 200
    assert headers["X-Test"] == "1"
    assert payload == b'{"accepted": true}'
