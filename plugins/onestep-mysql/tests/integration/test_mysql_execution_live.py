from __future__ import annotations

import asyncio
import os
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa

from onestep import (
    ExecutionClient,
    ExecutionCompletion,
    ExecutionRequest,
    ExecutionStatus,
)
from onestep_mysql import MySQLConnector
from onestep_mysql.execution_backend import (
    MySQLExecutionBackend,
    StaleExecutionLease,
    _assert_supported_mysql_server,
    _parse_mysql_server_version,
)


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


class _AsyncPauseAfterClaimSelect:
    """Hold the claim transaction open right after its SKIP LOCKED select.

    The machine's ``_claim`` opens its own transaction, so the pause is keyed
    on the statement shape that only the claim select has (a ``Select`` with
    ``FOR UPDATE SKIP LOCKED``) — on an empty range that is exactly the
    statement whose gap locks would block concurrent submits under MySQL's
    default ``REPEATABLE-READ`` (design §6.10).
    """

    def __init__(
        self,
        conn,
        *,
        paused: asyncio.Event,
        resume: asyncio.Event,
    ) -> None:
        self._conn = conn
        self._paused = paused
        self._resume = resume
        self._paused_once = False

    async def execute(self, statement, *args, **kwargs):
        result = await self._conn.execute(statement, *args, **kwargs)
        for_update = getattr(statement, "_for_update_arg", None)
        if (
            not self._paused_once
            and isinstance(statement, sa.sql.Select)
            and for_update is not None
            and for_update.skip_locked
        ):
            self._paused_once = True
            self._paused.set()
            try:
                await asyncio.wait_for(self._resume.wait(), 10)
            except asyncio.TimeoutError:
                raise TimeoutError("claim lock barrier timed out")
        return result


def test_concurrent_auto_create_is_serialized_live():
    async def scenario() -> None:
        execution_table, attempts_table = _names("autocreate")
        connectors = [MySQLConnector(_dsn()) for _ in range(6)]
        backends = [
            connector.execution_backend(
                table=execution_table,
                attempts_table=attempts_table,
            )
            for connector in connectors
        ]
        try:
            # §6.8: six engines racing on the same pair must serialize through
            # GET_LOCK instead of failing with (1050, "already exists").
            await asyncio.gather(*(backend.open() for backend in backends))
        finally:
            await _close_and_drop(connectors, (execution_table, attempts_table))

    asyncio.run(scenario())


def test_overlapping_table_pairs_open_concurrently_live():
    async def scenario() -> None:
        # §15.6-(a): overlapping pairs (shared executions table, different
        # attempts tables) derive *different* pair DDL locks, so concurrent
        # open() used to race on the shared CREATE TABLE (10/10: 8×1050 +
        # 2×1213). The fixed global DDL lock taken before the pair lock
        # serializes every execution DDL; both opens must now succeed with the
        # shared table created exactly once (structure mirrors the sequential
        # shared-schema case above).
        execution_table, first_attempts = _names("overlap")
        second_attempts = f"{first_attempts}_other"
        first_connector = MySQLConnector(_dsn())
        second_connector = MySQLConnector(_dsn())
        try:
            first = first_connector.execution_backend(
                table=execution_table, attempts_table=first_attempts
            )
            second = second_connector.execution_backend(
                table=execution_table, attempts_table=second_attempts
            )
            await asyncio.gather(first.open(), second.open())

            engine = sa.create_engine(_dsn(), future=True)
            try:
                with engine.begin() as conn:
                    rows = conn.execute(
                        sa.text(
                            "SELECT table_name, COUNT(*) FROM information_schema.tables "
                            "WHERE table_schema = DATABASE() "
                            "AND table_name IN (:e, :a1, :a2) GROUP BY table_name"
                        ),
                        {"e": execution_table, "a1": first_attempts, "a2": second_attempts},
                    ).all()
            finally:
                engine.dispose()
            counts = {row[0]: row[1] for row in rows}
            # Each physical table exists exactly once — no duplicate CREATE
            # slipped past the serialization.
            assert counts.get(execution_table) == 1
            assert counts.get(first_attempts) == 1
            assert counts.get(second_attempts) == 1

            # Both backends are functional against the shared schema.
            for backend, worker in ((first, "worker-a"), (second, "worker-b")):
                submitted = await backend.submit(
                    ExecutionRequest(namespace="agent-api", task_name="run_agent", payload={"v": 1})
                )
                [lease] = await backend.claim("agent-api", ("run_agent",), 1, 30, worker)
                assert lease.execution.id == submitted.id
        finally:
            await _close_and_drop(
                [first_connector, second_connector],
                (execution_table, first_attempts, second_attempts),
            )

    asyncio.run(scenario())


def test_two_execution_table_groups_coexist_live():
    async def scenario() -> None:
        # §6.6: CHECK names are schema-global in MySQL; derived names must let
        # two independent table groups live in the same database.
        first_exec, first_att = _names("groupa")
        second_exec, second_att = _names("groupb")
        first_connector = MySQLConnector(_dsn())
        second_connector = MySQLConnector(_dsn())
        try:
            first = first_connector.execution_backend(table=first_exec, attempts_table=first_att)
            second = second_connector.execution_backend(table=second_exec, attempts_table=second_att)
            await first.open()
            await second.open()
            for backend, namespace in ((first, "group-a"), (second, "group-b")):
                submitted = await backend.submit(
                    ExecutionRequest(namespace=namespace, task_name="run_agent", payload={"v": 1})
                )
                [lease] = await backend.claim(namespace, ("run_agent",), 1, 30, "worker")
                assert lease.execution.id == submitted.id
        finally:
            await _close_and_drop(
                [first_connector, second_connector],
                # executions first: the helper drops in reverse, so attempts
                # tables are dropped before the executions they reference.
                (first_exec, first_att, second_exec, second_att),
            )

    asyncio.run(scenario())


def test_custom_attempts_tables_can_share_schema_live():
    async def scenario() -> None:
        # §6.6/§6.11: one executions table plus two attempts tables needs FK
        # (and CHECK) names that differ per attempts table — a shared fixed
        # name fails with (1826, "Duplicate foreign key constraint name").
        execution_table, first_attempts = _names("shared")
        second_attempts = f"{first_attempts}_other"
        first_connector = MySQLConnector(_dsn())
        second_connector = MySQLConnector(_dsn())
        try:
            first = first_connector.execution_backend(
                table=execution_table,
                attempts_table=first_attempts,
            )
            second = second_connector.execution_backend(
                table=execution_table,
                attempts_table=second_attempts,
            )
            await first.open()
            await second.open()
            submitted = await first.submit(
                ExecutionRequest(namespace="agent-api", task_name="run_agent", payload={"v": 1})
            )
            [lease] = await second.claim("agent-api", ("run_agent",), 1, 30, "worker-b")
            assert lease.execution.id == submitted.id
        finally:
            await _close_and_drop(
                [first_connector, second_connector],
                (execution_table, first_attempts, second_attempts),
            )

    asyncio.run(scenario())


def test_long_attempts_table_creates_with_explicit_fk_live():
    async def scenario() -> None:
        # §6.11: at 58 characters InnoDB's implicit <table>_ibfk_1 name would
        # overflow the 64-character identifier limit (1059). The explicitly
        # derived FK name must keep the table creatable — this case is
        # invisible under default table names.
        execution_table = f"executions_{uuid.uuid4().hex[:12]}"
        attempts_table = f"attempts_{uuid.uuid4().hex[:12]}_" + "a" * 36
        assert len(attempts_table) == 58
        connector = MySQLConnector(_dsn())
        try:
            backend = connector.execution_backend(
                table=execution_table,
                attempts_table=attempts_table,
            )
            await backend.open()
            submitted = await backend.submit(
                ExecutionRequest(namespace="agent-api", task_name="run_agent", payload={"v": 1})
            )
            [lease] = await backend.claim("agent-api", ("run_agent",), 1, 30, "worker-a")
            assert lease.execution.id == submitted.id

            engine = sa.create_engine(_dsn(), future=True)
            try:
                with engine.begin() as conn:
                    # SHOW CREATE TABLE yields (table_name, create_statement).
                    ddl = conn.execute(
                        sa.text(f"SHOW CREATE TABLE `{attempts_table}`")
                    ).one()[1]
            finally:
                engine.dispose()
            # The FK constraint is present, explicitly named (fk_ prefix from
            # the derived name), and not an InnoDB-auto-generated _ibfk_N.
            assert "CONSTRAINT `fk_" in ddl
            assert "_ibfk_" not in ddl
        finally:
            await _close_and_drop([connector], (execution_table, attempts_table))

    asyncio.run(scenario())


def test_multiple_backends_claim_each_execution_once_live():
    async def scenario() -> None:
        execution_table, attempts_table = _names("claim")
        connectors = [MySQLConnector(_dsn()) for _ in range(4)]
        try:
            backends = [
                connector.execution_backend(table=execution_table, attempts_table=attempts_table)
                for connector in connectors
            ]
            await asyncio.gather(*(backend.open() for backend in backends))
            for index in range(12):
                await backends[0].submit(
                    ExecutionRequest(
                        namespace="agent-api",
                        task_name="run_agent",
                        payload={"index": index},
                    )
                )
            claimed = await asyncio.gather(
                *(
                    backend.claim("agent-api", ("run_agent",), 3, 30, f"worker-{number}")
                    for number, backend in enumerate(backends)
                )
            )
            ids = [lease.execution.id for batch in claimed for lease in batch]
            # §9.1: SKIP LOCKED claim — every execution claimed exactly once,
            # no duplicate lease tokens.
            assert len(ids) == 12
            assert len(set(ids)) == 12
            tokens = [lease.lease_token for batch in claimed for lease in batch]
            assert len(set(tokens)) == 12
        finally:
            await _close_and_drop(connectors, (execution_table, attempts_table))

    asyncio.run(scenario())


def test_heartbeat_prevents_takeover_live():
    async def scenario() -> None:
        execution_table, attempts_table = _names("heartbeat")
        first_connector = MySQLConnector(_dsn())
        second_connector = MySQLConnector(_dsn())
        try:
            first = first_connector.execution_backend(table=execution_table, attempts_table=attempts_table)
            second = second_connector.execution_backend(table=execution_table, attempts_table=attempts_table)
            await first.open()
            await second.open()
            await first.submit(
                ExecutionRequest(namespace="agent-api", task_name="run_agent", payload={"value": 1})
            )
            [lease] = await first.claim("agent-api", ("run_agent",), 1, 5, "worker-a")
            await first.heartbeat(lease.execution.id, lease.attempt_id, lease.lease_token, 5)
            assert await second.claim("agent-api", ("run_agent",), 1, 5, "worker-b") == ()
        finally:
            await _close_and_drop([first_connector, second_connector], (execution_table, attempts_table))

    asyncio.run(scenario())


def test_expired_lease_takeover_fences_old_worker_live():
    async def scenario() -> None:
        execution_table, attempts_table = _names("fence")
        first_connector = MySQLConnector(_dsn())
        second_connector = MySQLConnector(_dsn())
        try:
            first = first_connector.execution_backend(table=execution_table, attempts_table=attempts_table)
            second = second_connector.execution_backend(table=execution_table, attempts_table=attempts_table)
            await first.open()
            await second.open()
            await first.submit(
                ExecutionRequest(namespace="agent-api", task_name="run_agent", payload={"value": 1})
            )
            [old] = await first.claim("agent-api", ("run_agent",), 1, 0.2, "worker-a")
            await asyncio.sleep(0.35)
            [new] = await second.claim("agent-api", ("run_agent",), 1, 5, "worker-b")
            assert old.lease_token != new.lease_token
            with pytest.raises(StaleExecutionLease):
                await first.complete(
                    old.execution.id,
                    old.attempt_id,
                    old.lease_token,
                    ExecutionCompletion(status=ExecutionStatus.SUCCEEDED, result={"old": True}),
                )
            await second.complete(
                new.execution.id,
                new.attempt_id,
                new.lease_token,
                ExecutionCompletion(status=ExecutionStatus.SUCCEEDED, result={"new": True}),
            )
        finally:
            await _close_and_drop([first_connector, second_connector], (execution_table, attempts_table))

    asyncio.run(scenario())


def test_cancel_and_complete_race_has_one_terminal_winner_live():
    async def scenario() -> None:
        execution_table, attempts_table = _names("cancelrace")
        first_connector = MySQLConnector(_dsn())
        second_connector = MySQLConnector(_dsn())
        try:
            first = first_connector.execution_backend(table=execution_table, attempts_table=attempts_table)
            second = second_connector.execution_backend(table=execution_table, attempts_table=attempts_table)
            await first.open()
            await second.open()
            submitted = await first.submit(
                ExecutionRequest(namespace="agent-api", task_name="run_agent", payload={"value": 1})
            )
            [lease] = await first.claim("agent-api", ("run_agent",), 1, 30, "worker-a")
            start = asyncio.Event()

            async def cancel():
                await start.wait()
                return await second.request_cancel("agent-api", submitted.id, reason="stop")

            async def complete():
                await start.wait()
                try:
                    return await first.complete(
                        lease.execution.id,
                        lease.attempt_id,
                        lease.lease_token,
                        ExecutionCompletion(status=ExecutionStatus.SUCCEEDED, result={"ok": True}),
                    )
                except StaleExecutionLease:
                    return None

            cancel_task = asyncio.create_task(cancel())
            complete_task = asyncio.create_task(complete())
            start.set()
            cancelled, completed = await asyncio.gather(cancel_task, complete_task)
            final = await first.get("agent-api", submitted.id)
            assert final is not None and final.terminal
            assert final.status in {ExecutionStatus.SUCCEEDED, ExecutionStatus.CANCELLED}
            assert completed is None or completed.status is final.status
            if final.status is ExecutionStatus.CANCELLED:
                assert final.result is None
                async with first.engine.begin() as conn:
                    attempt = (await conn.execute(
                        sa.select(first.tables.attempts).where(
                            first.tables.attempts.c.id == lease.attempt_id
                        )
                    )).mappings().one()
                assert attempt["status"] == "cancelled"
        finally:
            await _close_and_drop([first_connector, second_connector], (execution_table, attempts_table))

    asyncio.run(scenario())


def test_concurrent_submit_with_same_idempotency_key_creates_one_execution_live():
    async def scenario() -> None:
        execution_table, attempts_table = _names("idem")
        connectors = [MySQLConnector(_dsn()) for _ in range(8)]
        try:
            backends = [
                connector.execution_backend(table=execution_table, attempts_table=attempts_table)
                for connector in connectors
            ]
            await asyncio.gather(*(backend.open() for backend in backends))
            clients = [
                ExecutionClient(backend, namespace="agent-api") for backend in backends
            ]
            results = await asyncio.gather(
                *(
                    client.submit("run_agent", {"prompt": "hello"}, idempotency_key="request-1")
                    for client in clients
                )
            )
            # §5.4: 8-way concurrent idempotent submit → exactly one row.
            assert len({result.id for result in results}) == 1
        finally:
            await _close_and_drop(connectors, (execution_table, attempts_table))

    asyncio.run(scenario())


def test_non_utc_expires_at_is_normalized_and_expired_rows_are_not_claimed_live():
    async def scenario() -> None:
        execution_table, attempts_table = _names("tz")
        connector = MySQLConnector(_dsn())
        try:
            backend = connector.execution_backend(table=execution_table, attempts_table=attempts_table)
            await backend.open()

            plus_eight = timezone(timedelta(hours=8))
            now_utc = datetime.now(timezone.utc)
            # Already expired by one hour, expressed in +08:00. Without the
            # write-boundary normalization MySQL would store the +08:00 wall
            # clock (≈ seven hours in the future) and claim the row (§6.3).
            expired_local = (now_utc - timedelta(hours=1)).astimezone(plus_eight)
            submitted = await backend.submit(
                ExecutionRequest(
                    namespace="agent-api",
                    task_name="run_agent",
                    payload={"value": 1},
                    expires_at=expired_local,
                )
            )
            assert await backend.claim("agent-api", ("run_agent",), 5, 30, "worker-a") == ()
            final = await backend.get("agent-api", submitted.id)
            assert final is not None and final.status is ExecutionStatus.EXPIRED

            async with backend.engine.begin() as conn:
                stored = (await conn.execute(
                    sa.select(backend.tables.executions.c.expires_at).where(
                        backend.tables.executions.c.id == submitted.id
                    )
                )).scalar_one()
            assert stored == expired_local.astimezone(timezone.utc).replace(tzinfo=None)

            # A non-UTC but unexpired expiry must not prevent the claim.
            future_local = (datetime.now(timezone.utc) + timedelta(hours=1)).astimezone(plus_eight)
            await backend.submit(
                ExecutionRequest(
                    namespace="agent-api",
                    task_name="run_agent",
                    payload={"value": 2},
                    expires_at=future_local,
                )
            )
            [lease] = await backend.claim("agent-api", ("run_agent",), 5, 30, "worker-b")
            assert lease.execution.payload == {"value": 2}
        finally:
            await _close_and_drop([connector], (execution_table, attempts_table))

    asyncio.run(scenario())


def test_datetime_microsecond_precision_is_immediately_claimable_live():
    async def scenario() -> None:
        execution_table, attempts_table = _names("fsp")
        submit_connector = MySQLConnector(_dsn())
        claim_connector = MySQLConnector(_dsn())
        try:
            # §6.2: with fsp=0 MySQL would round available_at 02:00:00.7 up to
            # 02:00:01, so the claim predicate (available_at <= now) at
            # 02:00:00.700001 would miss the just-submitted row. Both clocks
            # are injected, which makes the regression deterministic.
            base = datetime(2026, 9, 15, 2, 0, 0, 700000, tzinfo=timezone.utc)
            submit_backend = MySQLExecutionBackend(
                connector=submit_connector,
                table=execution_table,
                attempts_table=attempts_table,
                clock=lambda: base,
            )
            claim_backend = MySQLExecutionBackend(
                connector=claim_connector,
                table=execution_table,
                attempts_table=attempts_table,
                clock=lambda: base + timedelta(microseconds=1),
            )
            await submit_backend.open()
            await claim_backend.open()
            submitted = await submit_backend.submit(
                ExecutionRequest(namespace="agent-api", task_name="run_agent", payload={"value": 1})
            )

            async with submit_backend.engine.begin() as conn:
                stored = (await conn.execute(
                    sa.select(submit_backend.tables.executions.c.available_at).where(
                        submit_backend.tables.executions.c.id == submitted.id
                    )
                )).scalar_one()
            # Microseconds survived the round trip: not rounded to the second.
            assert stored == base.replace(tzinfo=None)

            [lease] = await claim_backend.claim("agent-api", ("run_agent",), 1, 30, "worker-a")
            assert lease.execution.id == submitted.id
        finally:
            await _close_and_drop(
                [submit_connector, claim_connector],
                (execution_table, attempts_table),
            )

    asyncio.run(scenario())


def test_server_version_gate_accepts_the_live_mysql_live():
    async def scenario() -> None:
        # §8.3: open() refuses MySQL servers older than 8.0.16 with an explicit
        # RuntimeError. Every live test exercises the positive path implicitly
        # (open() would fail below the floor); this asserts it explicitly — the
        # gate accepts the live server and returns its parsed version. The CI
        # matrix (8.0/8.4) runs this gate against both supported lines.
        execution_table, attempts_table = _names("vgate")
        connector = MySQLConnector(_dsn())
        try:
            backend = connector.execution_backend(
                table=execution_table,
                attempts_table=attempts_table,
            )
            await backend.open()  # RuntimeError below 8.0.16 would surface here
            async with backend.engine.connect() as conn:
                raw = (await conn.execute(sa.text("SELECT VERSION()"))).scalar_one()
            parsed = _parse_mysql_server_version(raw)
            assert parsed >= (8, 0, 16)
            assert _assert_supported_mysql_server(raw) == parsed
        finally:
            await _close_and_drop([connector], (execution_table, attempts_table))

    asyncio.run(scenario())


def test_sessions_are_pinned_to_utc_and_read_committed_live():
    async def scenario() -> None:
        execution_table, attempts_table = _names("session")
        connector = MySQLConnector(_dsn())
        try:
            backend = connector.execution_backend(table=execution_table, attempts_table=attempts_table)
            await backend.open()
            async with backend.engine.connect() as conn:
                time_zone = (await conn.execute(sa.text("SELECT @@session.time_zone"))).scalar_one()
                isolation = (
                    await conn.execute(sa.text("SELECT @@session.transaction_isolation"))
                ).scalar_one()
            # §6.4: session time zone pinned to UTC; §6.10: READ COMMITTED so
            # an empty claim range takes no gap locks.
            assert time_zone == "+00:00"
            assert isolation.replace("-", " ") == "READ COMMITTED"
        finally:
            await _close_and_drop([connector], (execution_table, attempts_table))

    asyncio.run(scenario())


def test_empty_range_claim_does_not_block_concurrent_submit_live():
    async def scenario() -> None:
        execution_table, attempts_table = _names("gaplock")
        first_connector = MySQLConnector(_dsn())
        second_connector = MySQLConnector(_dsn())
        try:
            first = first_connector.execution_backend(table=execution_table, attempts_table=attempts_table)
            second = second_connector.execution_backend(table=execution_table, attempts_table=attempts_table)
            await first.open()
            await second.open()

            executions = first.tables.executions
            attempts = first.tables.attempts
            # The claim predicate from the shared machine (§6.10): held open on
            # an *empty* range it must not take gap locks under READ COMMITTED.
            now = datetime.now(timezone.utc)
            claim_stmt = (
                sa.select(executions)
                .where(
                    executions.c.namespace == "agent-api",
                    executions.c.task_name.in_(("run_agent",)),
                    executions.c.status.in_((
                        ExecutionStatus.QUEUED.value,
                        ExecutionStatus.RETRYING.value,
                    )),
                    executions.c.available_at <= now,
                    sa.or_(
                        executions.c.expires_at.is_(None),
                        executions.c.expires_at > now,
                        first._lease_lost_retry_predicate(executions, attempts),
                    ),
                )
                .order_by(
                    executions.c.available_at,
                    executions.c.created_at,
                    executions.c.id,
                )
                .limit(1)
            ).with_for_update(skip_locked=True)

            paused = asyncio.Event()
            resume = asyncio.Event()

            async def hold_claim_range() -> None:
                async with first.engine.connect() as raw_conn:
                    wrapper = _AsyncPauseAfterClaimSelect(raw_conn, paused=paused, resume=resume)
                    async with raw_conn.begin():
                        await wrapper.execute(claim_stmt)

            hold_task = asyncio.create_task(hold_claim_range())
            await asyncio.wait_for(paused.wait(), 10)

            started = time.monotonic()
            await asyncio.wait_for(
                second.submit(
                    ExecutionRequest(
                        namespace="agent-api", task_name="run_agent", payload={"value": 1}
                    )
                ),
                timeout=10,
            )
            elapsed = time.monotonic() - started
            # §6.10: under REPEATABLE-READ this INSERT blocks on the claim's
            # gap locks until the holder releases; READ COMMITTED completes in
            # well under the 100ms budget.
            assert elapsed < 0.1, f"submit blocked for {elapsed:.3f}s behind an open claim"

            resume.set()
            await hold_task
        finally:
            await _close_and_drop([first_connector, second_connector], (execution_table, attempts_table))

    asyncio.run(scenario())


def test_json_metadata_expression_default_takes_effect_live():
    async def scenario() -> None:
        execution_table, attempts_table = _names("jsondefault")
        connector = MySQLConnector(_dsn())
        try:
            backend = connector.execution_backend(table=execution_table, attempts_table=attempts_table)
            await backend.open()
            # §6.1: the default must actually render as DEFAULT (JSON_OBJECT())
            # — a server_default assigned after Column construction silently
            # disappears, and a metadata-less INSERT then fails with 1364.
            execution_id = uuid.uuid4().hex
            async with backend.engine.begin() as conn:
                await conn.execute(
                    sa.text(
                        f"INSERT INTO `{execution_table}` "
                        "(id, namespace, task_name, status, payload, available_at, created_at, updated_at) "
                        "VALUES (:id, 'agent-api', 'run_agent', 'queued', '{}', UTC_TIMESTAMP(6), UTC_TIMESTAMP(6), UTC_TIMESTAMP(6))"
                    ),
                    {"id": execution_id},
                )
                stored = (await conn.execute(
                    sa.text(f"SELECT metadata FROM `{execution_table}` WHERE id = :id"),
                    {"id": execution_id},
                )).scalar_one()
                await conn.execute(
                    sa.text(f"DELETE FROM `{execution_table}` WHERE id = :id"),
                    {"id": execution_id},
                )
            assert (stored == {} or stored == "{}")
        finally:
            await _close_and_drop([connector], (execution_table, attempts_table))

    asyncio.run(scenario())


def test_client_submit_claim_complete_result_round_trip_live():
    async def scenario() -> None:
        execution_table, attempts_table = _names("roundtrip")
        api_connector = MySQLConnector(_dsn())
        worker_connector = MySQLConnector(_dsn())
        try:
            api_backend = api_connector.execution_backend(
                table=execution_table,
                attempts_table=attempts_table,
            )
            worker_backend = worker_connector.execution_backend(
                table=execution_table,
                attempts_table=attempts_table,
                auto_create=False,
            )
            await api_backend.open()
            client = ExecutionClient(api_backend, namespace="agent-api")
            submitted = await client.submit(
                "run_agent", {"prompt": "hello"}, idempotency_key="roundtrip-1"
            )
            assert submitted.status is ExecutionStatus.QUEUED

            [lease] = await worker_backend.claim("agent-api", ("run_agent",), 1, 30, "worker-1")
            assert lease.execution.payload == {"prompt": "hello"}
            assert lease.execution.metadata == {}  # server default round trip

            await worker_backend.complete(
                lease.execution.id,
                lease.attempt_id,
                lease.lease_token,
                ExecutionCompletion(status=ExecutionStatus.SUCCEEDED, result={"answer": 42}),
            )
            result = await client.result(submitted.id)
            assert result == {"answer": 42}
            final = await client.get(submitted.id)
            assert final is not None and final.status is ExecutionStatus.SUCCEEDED

            # Cancel on a queued execution settles immediately.
            queued = await client.submit("run_agent", {"prompt": "stop"})
            cancelled = await client.cancel(queued.id, reason="no longer needed")
            assert cancelled is not None and cancelled.status is ExecutionStatus.CANCELLED
            page = await client.list(limit=10)
            assert {item.id for item in page.items} >= {submitted.id, queued.id}
        finally:
            await _close_and_drop([api_connector, worker_connector], (execution_table, attempts_table))

    asyncio.run(scenario())
