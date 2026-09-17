"""End-to-end interop harness: TS submits, Python worker executes.

This is the definitive proof that the TypeScript client speaks the same
protocol as the Python runtime. It is driven by tests/e2e.test.ts, which:

  1. creates the execution tables via this script,
  2. submits an execution through the TS client,
  3. invokes this script to claim and run it as a real onestep worker,
  4. reads the result back through the TS client.

Usage:
    python clients/ts/tests/e2e_worker.py setup
    python clients/ts/tests/e2e_worker.py run --task <name> --once
"""

from __future__ import annotations

import argparse
import asyncio
import time
import os
import sys

DSN = os.environ.get(
    "ONESTEP_E2E_DSN",
    "postgresql://onestep:onestep@localhost:5432/onestep",
)
NAMESPACE = "e2e"

EXECUTIONS_TABLE = "onestep_executions"
ATTEMPTS_TABLE = "onestep_execution_attempts"


def _backend():
    from onestep_sql.postgres.execution_backend import PostgresExecutionBackend

    return PostgresExecutionBackend(
        dsn=DSN,
        table=EXECUTIONS_TABLE,
        attempts_table=ATTEMPTS_TABLE,
    )


async def setup() -> int:
    """Create the execution tables (idempotent via backend.open())."""
    backend = _backend()
    await backend.open()
    await backend.close()
    print("tables ready")
    return 0


async def reset() -> int:
    """Drop and recreate the execution tables for a clean run."""
    from sqlalchemy import text

    backend = _backend()
    backend._ensure_connector()
    engine = backend.engine
    async with engine.begin() as conn:
        await conn.execute(text(f'DROP TABLE IF EXISTS "{ATTEMPTS_TABLE}" CASCADE'))
        await conn.execute(text(f'DROP TABLE IF EXISTS "{EXECUTIONS_TABLE}" CASCADE'))
    await backend.open()
    await backend.close()
    print("tables reset")
    return 0


def run(task_name: str, wait_seconds: float) -> int:
    """Claim and execute pending work as a real onestep worker would.

    Synchronous on purpose: `OneStepApp.run()` owns its own event loop, so this
    must not be wrapped in asyncio.run().
    """
    from onestep import OneStepApp
    from onestep_sql.postgres.execution_source import PostgresExecutionSource

    app = OneStepApp("e2e-worker")

    source = PostgresExecutionSource(
        dsn=DSN,
        namespace=NAMESPACE,
        task_names=[task_name],
        table=EXECUTIONS_TABLE,
        attempts_table=ATTEMPTS_TABLE,
        poll_interval_s=0.2,
        batch_size=1,
        lease_duration_s=60.0,
        heartbeat_interval_s=10.0,
        worker_id="e2e-worker-1",
    )

    @app.task(name=task_name, source=source)
    async def handle(ctx, payload):
        # A real handler: echo the payload with a marker the test asserts on.
        return {"echo": payload, "worker": "python"}

    # Run the worker loop briefly, then stop. `app.run()` is a blocking call
    # that owns its own event loop, so the stop timer is armed from a thread.
    import threading

    def _stop_later():
        time.sleep(wait_seconds)
        app.request_shutdown()

    stopper = threading.Thread(target=_stop_later, daemon=True)
    stopper.start()
    app.run()
    print("worker finished")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["setup", "reset", "run"])
    parser.add_argument("--task", default="e2e.echo")
    parser.add_argument("--wait", type=float, default=6.0)
    args = parser.parse_args()

    if args.command == "setup":
        return asyncio.run(setup())
    if args.command == "reset":
        return asyncio.run(reset())
    return run(args.task, args.wait)


if __name__ == "__main__":
    sys.exit(main())
