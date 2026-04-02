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
    assert "Reporter: -" in captured.out
    assert "Resources: 0" in captured.out
    assert "Hooks: startup=0 shutdown=0 events=0" in captured.out
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
    assert summary["reporter"] is None
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


def test_cli_init_creates_minimal_project(tmp_path, monkeypatch, capsys) -> None:
    project_dir = tmp_path / "billing-sync"

    exit_code = main(["init", str(project_dir)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert f"Initialized OneStep project at {project_dir.resolve()}" in captured.out
    assert "Project: billing-sync" in captured.out
    assert "Package: billing_sync" in captured.out
    assert (project_dir / "pyproject.toml").exists()
    assert (project_dir / "worker.yaml").exists()
    assert (project_dir / "README.md").exists()
    assert (project_dir / "src" / "billing_sync" / "tasks" / "__init__.py").exists()
    assert (project_dir / "src" / "billing_sync" / "tasks" / "demo.py").exists()
    assert (project_dir / "src" / "billing_sync" / "transforms" / "__init__.py").exists()
    assert (project_dir / "src" / "billing_sync" / "transforms" / "demo.py").exists()
    assert (project_dir / "src" / "billing_sync" / "hooks.py").exists()

    monkeypatch.chdir(project_dir)
    monkeypatch.setattr(
        sys,
        "path",
        [
            entry
            for entry in sys.path
            if os.path.abspath(entry)
            not in {os.path.abspath(str(project_dir)), os.path.abspath(str(project_dir / "src"))}
        ],
    )
    clear_modules(
        monkeypatch,
        "billing_sync",
        "billing_sync.tasks",
        "billing_sync.tasks.demo",
        "billing_sync.transforms",
        "billing_sync.transforms.demo",
        "billing_sync.hooks",
    )

    exit_code = main(["check", "worker.yaml"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "App: billing-sync" in captured.out
    assert "handler=billing_sync.tasks.demo:run_demo" in captured.out


def test_cli_init_rejects_existing_files_without_force(tmp_path, capsys) -> None:
    project_dir = tmp_path / "existing-worker"
    project_dir.mkdir()
    existing_worker = project_dir / "worker.yaml"
    existing_worker.write_text("name: keep-me\n", encoding="utf-8")

    exit_code = main(["init", str(project_dir)])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert f"failed to initialize {project_dir}" in captured.err
    assert existing_worker.read_text(encoding="utf-8") == "name: keep-me\n"


def test_cli_init_force_overwrites_existing_files(tmp_path, capsys) -> None:
    project_dir = tmp_path / "existing-worker"
    project_dir.mkdir()
    existing_worker = project_dir / "worker.yaml"
    existing_worker.write_text("name: keep-me\n", encoding="utf-8")

    exit_code = main(["init", str(project_dir), "--force"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Project: existing-worker" in captured.out
    assert "apiVersion: onestep/v1alpha1" in existing_worker.read_text(encoding="utf-8")


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
    assert "Reporter: -" in captured.out
    assert "Resources: 2 (incoming<MemoryQueue>, processed<MemoryQueue>)" in captured.out
    assert "Hooks: startup=0 shutdown=0 events=0" in captured.out
    assert "- consume source=incoming<MemoryQueue> emit=processed<MemoryQueue>" in captured.out
    assert "retry=MaxAttempts" in captured.out
    assert "description='Consume queued messages from YAML config.'" in captured.out
    assert "handler=testsupport_yaml_cli:consume" in captured.out


def test_cli_check_loads_yaml_handlers_from_yaml_project_src_layout(tmp_path, monkeypatch, capsys) -> None:
    project_dir = tmp_path / "yaml-project"
    project_dir.mkdir()
    (project_dir / "pyproject.toml").write_text(
        "[project]\nname = 'yaml-project'\nversion = '0.0.0'\n",
        encoding="utf-8",
    )
    src_dir = project_dir / "src" / "demo_worker"
    src_dir.mkdir(parents=True)
    (src_dir / "__init__.py").write_text("", encoding="utf-8")
    (src_dir / "tasks.py").write_text(
        "\n".join(
            [
                "from onestep import OneStepApp",
                "",
                "async def consume(ctx, item):",
                "    return None",
                "",
            ]
        ),
        encoding="utf-8",
    )
    config_path = project_dir / "worker.yaml"
    config_path.write_text(
        json.dumps(
            {
                "name": "yaml-project-app",
                "resources": {
                    "incoming": {"type": "memory"},
                },
                "tasks": [
                    {
                        "name": "consume",
                        "source": "incoming",
                        "handler": "demo_worker.tasks:consume",
                    }
                ],
            }
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
                os.path.abspath(str(project_dir)),
                os.path.abspath(str(project_dir / "src")),
            }
        ],
    )
    clear_modules(monkeypatch, "demo_worker", "demo_worker.tasks")

    with registered_yaml_module():
        exit_code = main(["check", str(config_path)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "App: yaml-project-app" in captured.out
    assert "handler=demo_worker.tasks:consume" in captured.out


def test_cli_check_strict_loads_valid_yaml_target(capsys, tmp_path) -> None:
    config_path = tmp_path / "strict-app.yaml"
    config_path.write_text(
        json.dumps(
            {
                "apiVersion": "onestep/v1alpha1",
                "kind": "App",
                "app": {
                    "name": "yaml-strict-app",
                },
                "resources": {
                    "incoming": {"type": "memory"},
                },
                "tasks": [
                    {
                        "name": "consume",
                        "source": "incoming",
                        "handler": "testsupport_yaml_cli_strict:consume",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    async def consume(ctx, item):
        return None

    with registered_yaml_module(), registered_module("testsupport_yaml_cli_strict", consume=consume):
        exit_code = main(["check", "--strict", str(config_path)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "App: yaml-strict-app" in captured.out


def test_cli_check_strict_rejects_unknown_task_fields(capsys, tmp_path) -> None:
    config_path = tmp_path / "strict-invalid-task.yaml"
    config_path.write_text(
        json.dumps(
            {
                "apiVersion": "onestep/v1alpha1",
                "kind": "App",
                "name": "yaml-strict-invalid",
                "resources": {
                    "incoming": {"type": "memory"},
                },
                "tasks": [
                    {
                        "name": "consume",
                        "source": "incoming",
                        "handler": "testsupport_yaml_cli_invalid:consume",
                        "unexpected": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    async def consume(ctx, item):
        return None

    with registered_yaml_module(), registered_module("testsupport_yaml_cli_invalid", consume=consume):
        exit_code = main(["check", "--strict", str(config_path)])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert f"failed to load {config_path}" in captured.err
    assert "unsupported fields for tasks[0]" in captured.err


def test_cli_check_strict_rejects_mixed_app_and_legacy_root_fields(capsys, tmp_path) -> None:
    config_path = tmp_path / "strict-mixed.yaml"
    config_path.write_text(
        json.dumps(
            {
                "apiVersion": "onestep/v1alpha1",
                "kind": "App",
                "name": "legacy-name",
                "app": {
                    "name": "yaml-strict-mixed",
                },
                "tasks": [],
            }
        ),
        encoding="utf-8",
    )

    with registered_yaml_module():
        exit_code = main(["check", "--strict", str(config_path)])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "strict mode does not allow mixing 'app' with legacy top-level app fields" in captured.err


def test_cli_check_strict_rejects_unknown_reporter_fields(capsys, tmp_path) -> None:
    config_path = tmp_path / "strict-reporter.yaml"
    config_path.write_text(
        json.dumps(
            {
                "apiVersion": "onestep/v1alpha1",
                "kind": "App",
                "name": "yaml-strict-reporter",
                "reporter": {
                    "base_url": "https://control-plane.example.com",
                    "url": "https://wrong.example.com",
                },
                "tasks": [],
            }
        ),
        encoding="utf-8",
    )

    with registered_yaml_module():
        exit_code = main(["check", "--strict", str(config_path)])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "unsupported fields for reporter: url" in captured.err


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


def test_yaml_target_builds_mysql_rabbitmq_sqs_and_state_resources_via_refs(monkeypatch, tmp_path) -> None:
    class FakeCursorStore:
        def __init__(self, *, connector, kwargs):
            self.connector = connector
            self.kwargs = kwargs

        async def load(self, key: str):
            return None

        async def save(self, key: str, value):
            return None

    class FakeStateStore(FakeCursorStore):
        async def delete(self, key: str):
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

    class FakeSQSConnector:
        def __init__(self, region_name=None, options=None):
            self.region_name = region_name
            self.options = options

        def queue(self, url: str, **kwargs):
            return FakeQueue(url, connector=self, kind="sqs_queue", kwargs=kwargs)

    class FakeMySQLConnector:
        def __init__(self, dsn: str, **engine_options):
            self.dsn = dsn
            self.engine_options = engine_options
            self.cursor_stores = []
            self.state_stores = []

        def state_store(self, **kwargs):
            store = FakeStateStore(connector=self, kwargs=kwargs)
            self.state_stores.append(store)
            return store

        def cursor_store(self, **kwargs):
            store = FakeCursorStore(connector=self, kwargs=kwargs)
            self.cursor_stores.append(store)
            return store

        def incremental(self, **kwargs):
            return FakeSource(f"mysql.incremental:{kwargs['table']}", connector=self, kind="mysql_incremental", kwargs=kwargs)

        def table_sink(self, **kwargs):
            return FakeSink(f"mysql.table_sink:{kwargs['table']}", connector=self, kind="mysql_table_sink", kwargs=kwargs)

    monkeypatch.setattr(config_module, "RabbitMQConnector", FakeRabbitMQConnector)
    monkeypatch.setattr(config_module, "SQSConnector", FakeSQSConnector, raising=False)
    monkeypatch.setattr(config_module, "MySQLConnector", FakeMySQLConnector)

    config_path = tmp_path / "advanced.yaml"
    config_path.write_text(
        json.dumps(
            {
                "app": {
                    "name": "yaml-advanced",
                    "state": "app_state",
                    "config": {"environment": "test"},
                },
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
                    "app_state": {
                        "type": "mysql_state_store",
                        "connector": "db",
                        "table": "app_state",
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
                    "sqs": {
                        "type": "sqs",
                        "region_name": "ap-southeast-1",
                        "options": {"endpoint_url": "http://localstack:4566"},
                    },
                    "jobs_sqs_in": {
                        "type": "sqs_queue",
                        "connector": "sqs",
                        "url": "https://sqs.ap-southeast-1.amazonaws.com/123456789/jobs.fifo",
                        "message_group_id": "workers",
                        "heartbeat_interval_s": 15,
                        "heartbeat_visibility_timeout": 60,
                    },
                    "jobs_sqs_out": {
                        "type": "sqs_queue",
                        "connector": "sqs",
                        "url": "https://sqs.ap-southeast-1.amazonaws.com/123456789/processed.fifo",
                        "message_group_id": "workers",
                        "delete_batch_size": 5,
                    },
                },
                "tasks": [
                    {
                        "name": "sync_users",
                        "source": "users",
                        "handler": "testsupport_yaml_advanced:sync_users",
                        "emit": ["processed", "jobs_sqs_out"],
                    },
                    {
                        "name": "ingest_jobs",
                        "source": "jobs_in",
                        "handler": "testsupport_yaml_advanced:ingest_jobs",
                    },
                    {
                        "name": "ingest_sqs_jobs",
                        "source": "jobs_sqs_in",
                        "handler": "testsupport_yaml_advanced:ingest_sqs_jobs",
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

    async def ingest_sqs_jobs(ctx, item):
        return None

    with registered_yaml_module(), registered_module(
        "testsupport_yaml_advanced",
        sync_users=sync_users,
        ingest_jobs=ingest_jobs,
        ingest_sqs_jobs=ingest_sqs_jobs,
    ):
        app = OneStepApp.load(str(config_path))

    sync_task = app.tasks[0]
    ingest_task = app.tasks[1]
    ingest_sqs_task = app.tasks[2]

    assert app.config == {"environment": "test", "config_path": str(config_path)}
    assert app.state is sync_task.source.connector.state_stores[0]
    assert app.state.kwargs["table"] == "app_state"
    assert sync_task.source.kind == "mysql_incremental"
    assert sync_task.source.kwargs["cursor"] == ("updated_at", "id")
    assert sync_task.source.kwargs["state"] is sync_task.source.connector.cursor_stores[0]
    assert sync_task.source.kwargs["state_key"] == "users-sync"
    assert sync_task.source.connector.engine_options == {"pool_recycle": 3600}
    assert sync_task.sinks[0].kind == "mysql_table_sink"
    assert sync_task.sinks[0].connector is sync_task.source.connector
    assert sync_task.sinks[1].kind == "sqs_queue"
    assert sync_task.sinks[1].connector.region_name == "ap-southeast-1"
    assert sync_task.sinks[1].connector.options == {"endpoint_url": "http://localstack:4566"}
    assert sync_task.sinks[1].kwargs["message_group_id"] == "workers"
    assert sync_task.sinks[1].kwargs["delete_batch_size"] == 5
    assert ingest_task.source.kind == "rabbitmq_queue"
    assert ingest_task.source.name == "incoming_jobs"
    assert ingest_task.source.connector.url == "amqp://guest:guest@localhost/"
    assert ingest_task.source.connector.options == {"client_properties": {"connection_name": "yaml-worker"}}
    assert ingest_task.source.kwargs["exchange"] == "jobs.events"
    assert ingest_task.source.kwargs["prefetch"] == 50
    assert ingest_sqs_task.source.kind == "sqs_queue"
    assert ingest_sqs_task.source.name == "https://sqs.ap-southeast-1.amazonaws.com/123456789/jobs.fifo"
    assert ingest_sqs_task.source.kwargs["heartbeat_interval_s"] == 15
    assert ingest_sqs_task.source.kwargs["heartbeat_visibility_timeout"] == 60


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


def test_yaml_target_supports_resources_app_hooks_task_config_and_task_hooks(tmp_path) -> None:
    config_path = tmp_path / "hooks.yaml"
    config_path.write_text(
        json.dumps(
            {
                "app": {
                    "name": "yaml-hooks",
                },
                "resources": {
                    "incoming": {"type": "memory"},
                    "processed": {"type": "memory"},
                },
                "hooks": {
                    "startup": {
                        "ref": "testsupport_yaml_hooks:seed",
                        "params": {"value": 5},
                    },
                    "shutdown": "testsupport_yaml_hooks:teardown",
                    "events": {
                        "ref": "testsupport_yaml_hooks:observe",
                        "params": {"label": "audit"},
                    },
                },
                "tasks": [
                    {
                        "name": "consume",
                        "source": "incoming",
                        "emit": ["processed"],
                        "config": {"mode": "sync"},
                        "metadata": {"owner": "data-platform"},
                        "handler": {
                            "ref": "testsupport_yaml_hooks:consume",
                            "params": {"factor": 2},
                        },
                        "hooks": {
                            "before": {
                                "ref": "testsupport_yaml_hooks:before",
                                "params": {"label": "pre"},
                            },
                            "after_success": "testsupport_yaml_hooks:after",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    seen = []

    async def seed(app, *, value: int):
        seen.append(("startup", sorted(app.resources)))
        await app.resources["incoming"].publish({"value": value})

    def teardown(app):
        seen.append(("shutdown", app.name))

    def observe(event, *, label: str):
        if event.kind.value == "succeeded":
            seen.append(("event", label, event.task))

    async def before(ctx, payload, *, label: str):
        seen.append(("before", label, ctx.task_config["mode"], payload["value"]))

    async def consume(ctx, item, *, factor: int):
        seen.append(("handler", ctx.task_config["mode"], item["value"], sorted(ctx.resources)))
        ctx.app.request_shutdown()
        return {"value": item["value"] * factor}

    def after(ctx, payload, result):
        seen.append(("after", payload["value"], result["value"]))

    async def scenario() -> None:
        with registered_yaml_module(), registered_module(
            "testsupport_yaml_hooks",
            seed=seed,
            teardown=teardown,
            observe=observe,
            before=before,
            consume=consume,
            after=after,
        ):
            app = OneStepApp.load(str(config_path))
            assert sorted(app.resources) == ["incoming", "processed"]
            assert app.tasks[0].config == {"mode": "sync"}
            assert app.tasks[0].metadata == {"owner": "data-platform"}
            assert app.tasks[0].handler_ref == "testsupport_yaml_hooks:consume"
            await app.serve()
            deliveries = await app.resources["processed"].fetch(1)
            assert len(deliveries) == 1
            assert deliveries[0].payload == {"value": 10}

    asyncio.run(scenario())
    assert seen == [
        ("startup", ["incoming", "processed"]),
        ("before", "pre", "sync", 5),
        ("handler", "sync", 5, ["incoming", "processed"]),
        ("after", 5, 10),
        ("event", "audit", "consume"),
        ("shutdown", "yaml-hooks"),
    ]


def test_yaml_task_failure_hooks_receive_failure_and_ignore_secondary_failures(tmp_path) -> None:
    config_path = tmp_path / "failure-hooks.yaml"
    config_path.write_text(
        json.dumps(
            {
                "name": "yaml-failure-hooks",
                "resources": {
                    "incoming": {"type": "memory"},
                },
                "hooks": {
                    "startup": "testsupport_yaml_failure_hooks:seed",
                },
                "tasks": [
                    {
                        "name": "explode",
                        "source": "incoming",
                        "handler": "testsupport_yaml_failure_hooks:explode",
                        "hooks": {
                            "on_failure": [
                                "testsupport_yaml_failure_hooks:capture_failure",
                                "testsupport_yaml_failure_hooks:broken_failure",
                            ]
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    seen = []

    async def seed(app):
        await app.resources["incoming"].publish({"id": 7})

    async def explode(ctx, item):
        raise RuntimeError("boom")

    async def capture_failure(ctx, payload, failure):
        seen.append(("capture", payload["id"], failure.kind.value, failure.exception_type, failure.message))
        ctx.app.request_shutdown()

    def broken_failure(ctx, payload, failure):
        seen.append(("broken", failure.kind.value))
        raise RuntimeError("hook observer broke")

    async def scenario() -> None:
        with registered_yaml_module(), registered_module(
            "testsupport_yaml_failure_hooks",
            seed=seed,
            explode=explode,
            capture_failure=capture_failure,
            broken_failure=broken_failure,
        ):
            app = OneStepApp.load(str(config_path))
            await app.serve()

    asyncio.run(scenario())
    assert seen == [
        ("capture", 7, "error", "RuntimeError", "boom"),
        ("broken", "error"),
    ]


def test_yaml_target_attaches_reporter_from_env(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "reporter-bool.yaml"
    config_path.write_text(
        json.dumps(
            {
                "name": "yaml-reporter",
                "reporter": True,
                "tasks": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ONESTEP_CONTROL_URL", "https://control-plane.example.com")
    monkeypatch.setenv("ONESTEP_CONTROL_TOKEN", "secret-token")
    created = []

    class FakeReporter:
        def __init__(self, config):
            self.config = config
            self.app = None
            created.append(self)

        def attach(self, app):
            self.app = app
            app.on_startup(lambda: None)
            app.on_shutdown(lambda: None)
            app.on_event(lambda event: None)
            return self

    monkeypatch.setattr(config_module, "ControlPlaneReporter", FakeReporter)

    with registered_yaml_module():
        app = OneStepApp.load(str(config_path))

    assert len(created) == 1
    reporter = created[0]
    assert reporter.app is app
    assert reporter.config.base_url == "https://control-plane.example.com"
    assert reporter.config.token == "secret-token"
    assert reporter.config.service_name == "yaml-reporter"
    assert app.describe()["reporter"] == {
        "type": "control_plane",
        "base_url": "https://control-plane.example.com",
        "service_name": "yaml-reporter",
    }
    assert app.describe()["hooks"] == {"startup": 1, "shutdown": 1, "events": 1}


def test_yaml_target_reporter_mapping_overrides_env_defaults(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "reporter-mapping.yaml"
    config_path.write_text(
        json.dumps(
            {
                "name": "yaml-reporter-overrides",
                "reporter": {
                    "base_url": "https://yaml-control-plane.example.com",
                    "service_name": "billing-sync-worker",
                },
                "tasks": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ONESTEP_CONTROL_URL", "https://env-control-plane.example.com")
    monkeypatch.setenv("ONESTEP_CONTROL_TOKEN", "env-token")
    created = []

    class FakeReporter:
        def __init__(self, config):
            self.config = config
            created.append(self)

        def attach(self, app):
            return self

    monkeypatch.setattr(config_module, "ControlPlaneReporter", FakeReporter)

    with registered_yaml_module():
        OneStepApp.load(str(config_path))

    assert len(created) == 1
    assert created[0].config.base_url == "https://yaml-control-plane.example.com"
    assert created[0].config.token == "env-token"
    assert created[0].config.service_name == "billing-sync-worker"


def test_cli_check_prints_reporter_summary_for_yaml_target(monkeypatch, tmp_path, capsys) -> None:
    config_path = tmp_path / "reporter-summary.yaml"
    config_path.write_text(
        json.dumps(
            {
                "name": "yaml-reporter-summary",
                "reporter": {
                    "base_url": "https://yaml-control-plane.example.com",
                    "service_name": "billing-sync-worker",
                },
                "tasks": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ONESTEP_CONTROL_TOKEN", "env-token")

    class FakeReporter:
        def __init__(self, config):
            self.config = config

        def attach(self, app):
            return self

    monkeypatch.setattr(config_module, "ControlPlaneReporter", FakeReporter)

    with registered_yaml_module():
        exit_code = main(["check", str(config_path)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert (
        "Reporter: control_plane service=billing-sync-worker "
        "base_url=https://yaml-control-plane.example.com"
    ) in captured.out
