"""Live MySQL integration tests for the tracked execution source (Phase 3).

Mirrors the source-level part of the PostgreSQL live suite
(``test_postgres_execution_live.py``) against the real MySQL container:

* the runnable YAML end-to-end chain — ``OneStepApp`` registers a
  ``mysql_execution_source`` resource built by strict YAML, a submitted
  execution is claimed through the source, completed by the runtime, and the
  API client reads the result back (design §10.2);
* correlation metadata on fetched envelopes;
* two sources claiming disjoint executions from the same tables.

Initialization is strictly sequential whenever more than one backend/source is
involved (OneStepApp resource registration is sequential too); the
overlapping-table-pair concurrent-``open()`` race is a recorded open question
(design §15.6) and deliberately never exercised here.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from uuid import UUID

import pytest
import sqlalchemy as sa

from onestep import (
    ExecutionClient,
    ExecutionRequest,
    ExecutionStatus,
    OneStepApp,
)
from onestep.config import load_app_config
from onestep.execution import ExecutionCompletion
from onestep.runtime import TaskRunner
from onestep_mysql import MySQLConnector
from onestep_mysql.execution_source import MySQLExecutionSource


pytestmark = pytest.mark.integration


if not os.getenv("ONESTEP_MYSQL_DSN"):
    pytest.skip("set ONESTEP_MYSQL_DSN to run MySQL integration tests", allow_module_level=True)


def _dsn() -> str:
    return os.environ["ONESTEP_MYSQL_DSN"]


def _names(prefix: str) -> tuple[str, str]:
    suffix = uuid.uuid4().hex[:12]
    return f"{prefix}_executions_{suffix}", f"{prefix}_attempts_{suffix}"


async def _close_and_drop(connectors: list[MySQLConnector], tables: tuple[str, ...]) -> None:
    engine = sa.create_engine(_dsn(), future=True)
    try:
        with engine.begin() as conn:
            for table in reversed(tables):
                conn.execute(sa.text(f"DROP TABLE IF EXISTS `{table}`"))
    finally:
        engine.dispose()
    await asyncio.gather(*(connector.close() for connector in connectors))


def test_yaml_e2e_submit_claim_result_live():
    """Strict YAML → OneStepApp resources → submit → source claim → result."""

    async def scenario() -> None:
        execution_table, attempts_table = _names("yamlsrc")
        app = load_app_config(
            {
                "apiVersion": "onestep/v1alpha1",
                "kind": "App",
                "app": {"name": "mysql-execution-yaml-e2e"},
                "resources": {
                    "db": {"type": "mysql", "dsn": _dsn()},
                    "jobs": {
                        "type": "mysql_execution_source",
                        "connector": "db",
                        "namespace": "agent-api",
                        "task_names": ["run_agent"],
                        "table": execution_table,
                        "attempts_table": attempts_table,
                        "batch_size": 4,
                        "poll_interval_s": 0.5,
                        "lease_duration_s": 90,
                        "heartbeat_interval_s": 30,
                        "worker_id": "yaml-worker-1",
                        "auto_create": True,
                    },
                },
                "tasks": [],
            },
            strict=True,
        )
        source = app.resources["jobs"]
        connector = app.resources["db"]
        assert isinstance(source, MySQLExecutionSource)
        assert source.backend.connector is connector
        assert source.name == "mysql.execution:agent-api"

        await source.open()
        try:
            client = ExecutionClient(source.backend, namespace="agent-api")
            submitted = await client.submit(
                "run_agent", {"prompt": "hello"}, idempotency_key="yaml-e2e-1"
            )
            assert submitted.status is ExecutionStatus.QUEUED

            [delivery] = await source.fetch(1)

            @app.task(name="run_agent", source=source)
            async def run_agent(ctx, payload):
                return {"answer": 42}

            await TaskRunner(app, app.tasks[0])._handle_delivery(delivery)

            result = await client.result(submitted.id)
            assert result == {"answer": 42}
            final = await client.get(submitted.id)
            assert final is not None
            assert final.status is ExecutionStatus.SUCCEEDED
        finally:
            await source.close()
            await _close_and_drop([connector], (execution_table, attempts_table))

    asyncio.run(scenario())


def test_source_fetch_carries_execution_correlation_metadata_live():
    async def scenario() -> None:
        execution_table, attempts_table = _names("srcmeta")
        connector = MySQLConnector(_dsn())
        try:
            backend = connector.execution_backend(
                table=execution_table,
                attempts_table=attempts_table,
            )
            # ``MySQLExecutionBackend.source()`` (the ``_make_source`` seam) is
            # still a Phase 2 placeholder, so the backend-named source is
            # constructed directly over the same backend.
            source = MySQLExecutionSource(
                backend=backend,
                namespace="agent-api",
                task_names=("run_agent",),
                worker_id="worker-1",
            )
            await source.open()
            try:
                client = ExecutionClient(backend, namespace="agent-api")
                submitted = await client.submit("run_agent", {"prompt": "hello"})
                [delivery] = await source.fetch(1)
                correlation = delivery.envelope.meta["onestep.execution"]
                assert correlation["id"] == str(submitted.id)
                assert UUID(correlation["attempt_id"])
                assert delivery.envelope.attempts == 0
                assert delivery.payload == {"prompt": "hello"}

                await delivery.complete_execution(
                    ExecutionCompletion(
                        status=ExecutionStatus.SUCCEEDED,
                        result={"answer": 7},
                    )
                )
                assert await client.result(submitted.id) == {"answer": 7}
            finally:
                await source.close()
        finally:
            await _close_and_drop([connector], (execution_table, attempts_table))

    asyncio.run(scenario())


def test_two_sources_claim_each_execution_once_live():
    async def scenario() -> None:
        execution_table, attempts_table = _names("srcclaim")
        first_connector = MySQLConnector(_dsn())
        second_connector = MySQLConnector(_dsn())
        try:
            first_backend = first_connector.execution_backend(
                table=execution_table,
                attempts_table=attempts_table,
            )
            second_backend = second_connector.execution_backend(
                table=execution_table,
                attempts_table=attempts_table,
                auto_create=False,
            )
            first = MySQLExecutionSource(
                backend=first_backend,
                namespace="agent-api",
                task_names=("run_agent",),
                worker_id="worker-a",
            )
            second = MySQLExecutionSource(
                backend=second_backend,
                namespace="agent-api",
                task_names=("run_agent",),
                worker_id="worker-b",
            )
            # Sequential initialization on purpose (design §15.6: overlapping
            # or shared-table concurrent open() is not exercised).
            await first.open()
            await second.open()
            try:
                for index in range(6):
                    await first_backend.submit(
                        ExecutionRequest(
                            namespace="agent-api",
                            task_name="run_agent",
                            payload={"index": index},
                        )
                    )
                [first_batch, second_batch] = await asyncio.gather(
                    first.fetch(3),
                    second.fetch(3),
                )
                claimed = [d.execution_id for d in (*first_batch, *second_batch)]
                assert len(claimed) == 6
                assert len(set(claimed)) == 6
            finally:
                await first.close()
                await second.close()
        finally:
            await _close_and_drop(
                [first_connector, second_connector], (execution_table, attempts_table)
            )

    asyncio.run(scenario())
