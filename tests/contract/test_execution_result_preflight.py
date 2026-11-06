"""Result failures must precede all automatic emit side effects (#225)."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
import sqlalchemy as sa

from onestep import OneStepApp, MemoryQueue
from onestep.connectors.base import Delivery, Sink
from onestep.envelope import Envelope
from onestep.execution import (
    ExecutionCompletion,
    ExecutionRequest,
    ExecutionStatus,
    ManagedExecutionDelivery,
)
from onestep.retry import MaxAttempts
from onestep.runtime.executor import DeliveryExecutor


@pytest.mark.parametrize("backend_name", ["mysql", "postgres"])
@pytest.mark.parametrize("failure", ["size", "encoding", "valid"])
def test_sql_result_preflight_prevents_emit_on_every_attempt(
    tmp_path, backend_name, failure
):
    async def run():
        if backend_name == "mysql":
            from onestep_sql.mysql import MySQLConnector as Connector
        else:
            from onestep_sql.postgres import PostgresConnector as Connector
        connector = Connector(f"sqlite:///{tmp_path / 'execution.db'}")
        backend = connector.execution_backend(max_result_bytes=1024)
        source = backend.source(
            namespace="test",
            task_names=("job",),
            worker_id="test",
            heartbeat_interval_s=1,
            lease_duration_s=30,
        )
        table = sa.Table(
            "writes",
            sa.MetaData(),
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("n", sa.Integer),
        )
        try:
            async with connector.engine.begin() as conn:
                await conn.run_sync(table.metadata.create_all)
                await conn.execute(table.insert(), {"id": 1, "n": 0})
            await source.open()
            sink = connector.table_sink(
                table="writes",
                mode="update",
                keys=("id",),
                update_columns=(),
                update_expr={"n": "n + 1"},
            )
            result = (
                "x" * 2048
                if failure == "size"
                else object()
                if failure == "encoding"
                else {"ok": True}
            )
            # Transform output is deliberately tiny: it is the stored result,
            # not just the emitted envelope, that must pass preflight.
            app = OneStepApp("result-preflight")
            from onestep.task import EmitBinding

            @app.task(
                source=source,
                emit=EmitBinding(
                    sink=sink, transform=lambda ctx, payload, result: {"id": 1}
                ),
                retry=MaxAttempts(max_attempts=2, delay_s=0),
            )
            async def job(ctx, payload):
                return result

            submitted = await backend.submit(
                ExecutionRequest(namespace="test", task_name="job", payload={})
            )
            for attempt in range(1 if failure == "valid" else 2):
                [delivery] = await source.fetch(1)
                outcome = await DeliveryExecutor(app, app.tasks[0]).execute(delivery)
                if failure == "valid":
                    assert outcome.completion == "succeeded"
                else:
                    assert outcome.failure_stage == "result_validation"
                    assert outcome.sinks_succeeded == []
                async with connector.engine.connect() as conn:
                    assert (await conn.execute(sa.select(table.c.n))).scalar_one() == (
                        1 if failure == "valid" else 0
                    )
            execution = await backend.get("test", submitted.id)
            assert execution.status is (
                ExecutionStatus.SUCCEEDED
                if failure == "valid"
                else ExecutionStatus.FAILED
            )
        finally:
            await source.close()
            await connector.close()

    asyncio.run(run())


class LegacyManagedDelivery(Delivery):
    def __init__(self):
        super().__init__(Envelope(body={}))
        self.execution_id = uuid4()
        self.attempt_id = uuid4()
        self.cancel_requested = False
        self.completion = None

    async def ack(self):
        raise AssertionError("managed completion should be used")

    async def retry(self, *, delay_s=None):
        raise AssertionError("managed completion should be used")

    async def fail(self, exc=None):
        raise AssertionError("managed completion should be used")

    async def complete_execution(self, completion: ExecutionCompletion):
        self.completion = completion


class RecordingSink(Sink):
    def __init__(self):
        super().__init__("recording")
        self.sent = []

    async def send(self, envelope):
        self.sent.append(envelope.body)


def test_legacy_managed_delivery_does_not_require_preflight():
    async def run():
        delivery = LegacyManagedDelivery()
        assert isinstance(delivery, ManagedExecutionDelivery)
        sink = RecordingSink()
        app = OneStepApp("legacy")

        @app.task(source=MemoryQueue("legacy"), emit=sink)
        async def job(ctx, payload):
            return {"ok": True}

        outcome = await DeliveryExecutor(app, app.tasks[0]).execute(delivery)
        assert outcome.completion == "succeeded"
        assert sink.sent == [{"ok": True}]
        assert delivery.completion.status is ExecutionStatus.SUCCEEDED

    asyncio.run(run())


@pytest.mark.parametrize("backend_name", ["mysql", "postgres"])
def test_completion_rechecks_result_after_successful_preflight(tmp_path, backend_name):
    async def run():
        from onestep.execution import ExecutionEncodingError

        if backend_name == "mysql":
            from onestep_sql.mysql import MySQLConnector as Connector
        else:
            from onestep_sql.postgres import PostgresConnector as Connector
        connector = Connector(f"sqlite:///{tmp_path / 'direct.db'}")
        backend = connector.execution_backend(max_result_bytes=1024)
        source = backend.source(
            namespace="test",
            task_names=("job",),
            worker_id="test",
            heartbeat_interval_s=1,
            lease_duration_s=30,
        )
        try:
            await source.open()
            await backend.submit(
                ExecutionRequest(namespace="test", task_name="job", payload={})
            )
            [delivery] = await source.fetch(1)
            await delivery.start_processing()
            result = {"value": "small"}
            await delivery.validate_execution_result(result)
            result["value"] = "x" * 2048
            with pytest.raises(ExecutionEncodingError):
                await delivery.complete_execution(
                    ExecutionCompletion(status=ExecutionStatus.SUCCEEDED, result=result)
                )
            await delivery.complete_execution(
                ExecutionCompletion(status=ExecutionStatus.FAILED)
            )
        finally:
            await source.close()
            await connector.close()

    asyncio.run(run())
