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
import json
import time
import os
import sys
from datetime import datetime

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


def describe_types(value):
    """Expose Python numeric types, which JS numbers cannot distinguish."""
    if isinstance(value, dict):
        return {key: describe_types(item) for key, item in value.items()}
    if isinstance(value, list):
        return [describe_types(item) for item in value]
    return {"type": type(value).__name__, "repr": repr(value)}


async def interop() -> int:
    """Exercise the production Python client using the original JSON spelling."""
    from onestep.execution import ExecutionClient, ExecutionConflict

    requests = json.load(sys.stdin)
    backend = _backend()
    await backend.open()
    results = []
    try:
        for request in requests:
            client = ExecutionClient(backend, namespace=request["namespace"])
            if request.get("operation") == "get":
                execution = await client.get(request["id"])
                assert execution is not None
            else:
                payload = json.loads(request["payloadJson"])
                metadata = json.loads(request["metadataJson"])
                if request.get("operation") == "resubmit-stored":
                    stored = await client.get(request["id"])
                    assert stored is not None
                    payload, metadata = stored.payload, stored.metadata
                expiry = request.get("expiresAt")
                try:
                    execution = await client.submit(
                        request["taskName"], payload, metadata=metadata,
                        idempotency_key=request["idempotencyKey"],
                        delay_s=request.get("delayS"),
                        expires_at=None if expiry is None else datetime.fromisoformat(expiry.replace("Z", "+00:00")),
                    )
                except ExecutionConflict:
                    if not request.get("expectConflict"):
                        raise
                    results.append({"conflict": True})
                    continue
                assert not request.get("expectConflict"), "expected an idempotency conflict"
            results.append({"id": str(execution.id), "payloadTypes": describe_types(execution.payload),
                            "metadataTypes": describe_types(execution.metadata),
                            "expiresAt": execution.expires_at.isoformat() if execution.expires_at else None})
    finally:
        await backend.close()
    print(json.dumps(results))
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
        if task_name == "e2e.json":
            return payload
        return {"echo": payload, "worker": "python", "types": describe_types(payload)}

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
    parser.add_argument("command", choices=["setup", "reset", "run", "interop"])
    parser.add_argument("--task", default="e2e.echo")
    parser.add_argument("--wait", type=float, default=6.0)
    args = parser.parse_args()

    if args.command == "setup":
        return asyncio.run(setup())
    if args.command == "reset":
        return asyncio.run(reset())
    if args.command == "interop":
        return asyncio.run(interop())
    return run(args.task, args.wait)


if __name__ == "__main__":
    sys.exit(main())
