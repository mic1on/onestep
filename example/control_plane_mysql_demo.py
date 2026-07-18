"""Control-plane-facing demo: interval source -> handler -> MySQL sink.

Pipeline:
    1. Source: IntervalSource emits one row every 5 seconds.
    2. Handler: enriches the row with a hash and a timestamp.
    3. Sink:   writes the enriched row into a MySQL table via MySQLConnector.table_sink.

When no MYSQL_DSN is provided, the demo starts a local MySQL Docker container,
waits for it to become reachable, and creates the target table automatically.

If control-plane env is present, a `ControlPlaneReporter` is attached so the
task, its source/sink topology, throughput, retries, and dead letters are
visible in the onestep control plane.

Optional env:
    ONESTEP_CONTROL_PLANE_URL     control-plane base URL (http://... or ws://...)
    ONESTEP_CONTROL_PLANE_TOKEN   control-plane auth token
    MYSQL_DSN                     external MySQL DSN; skips Docker auto-start
    CP_MYSQL_DEMO_START_MYSQL     set to 0/false/no/off to skip Docker auto-start
    CP_MYSQL_DEMO_MYSQL_PORT      host port for the Docker MySQL container
    CP_MYSQL_DEMO_CONTAINER       Docker container name

Cleanup:
    docker rm -f onestep-cp-mysql-demo

Run:
    uv run onestep run example.control_plane_mysql_demo:app
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import subprocess
import time
from datetime import datetime, timezone

import sqlalchemy as sa
from onestep import (
    ControlPlaneReporter,
    ControlPlaneReporterConfig,
    IntervalSource,
    MaxAttempts,
    OneStepApp,
    __version__ as ONESTEP_VERSION,
)
from onestep_mysql import MySQLConnector

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

SERVICE_NAME = os.getenv("ONESTEP_SERVICE_NAME", "cp-mysql-demo")
MYSQL_HOST = os.getenv("CP_MYSQL_DEMO_MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = int(os.getenv("CP_MYSQL_DEMO_MYSQL_PORT", "3306"))
MYSQL_DATABASE = os.getenv("CP_MYSQL_DEMO_MYSQL_DATABASE", "app")
MYSQL_USER = os.getenv("CP_MYSQL_DEMO_MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("CP_MYSQL_DEMO_MYSQL_PASSWORD", "root")
MYSQL_DSN = os.getenv(
    "MYSQL_DSN",
    f"mysql+pymysql://{MYSQL_USER}:{MYSQL_PASSWORD}@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DATABASE}",
)
INTERVAL_SECONDS = int(os.getenv("CP_MYSQL_DEMO_INTERVAL_S", "5"))
TARGET_TABLE = os.getenv("CP_MYSQL_DEMO_TABLE", "cp_demo_events")
MYSQL_CONTAINER = os.getenv("CP_MYSQL_DEMO_CONTAINER", "onestep-cp-mysql-demo")
MYSQL_IMAGE = os.getenv("CP_MYSQL_DEMO_MYSQL_IMAGE", "mysql:8.4")
MYSQL_WAIT_TIMEOUT_S = float(os.getenv("CP_MYSQL_DEMO_MYSQL_WAIT_TIMEOUT_S", "90"))
START_MYSQL = os.getenv(
    "CP_MYSQL_DEMO_START_MYSQL",
    "0" if "MYSQL_DSN" in os.environ else "1",
).strip().lower() not in {"0", "false", "no", "off"}
CONTROL_PLANE_ENABLED = bool(
    os.getenv("ONESTEP_CONTROL_PLANE_URL") or os.getenv("ONESTEP_CONTROL_URL")
) and bool(os.getenv("ONESTEP_CONTROL_PLANE_TOKEN") or os.getenv("ONESTEP_CONTROL_TOKEN"))

app = OneStepApp(SERVICE_NAME)

# MySQL connector + sink. `mode="upsert"` with `keys` makes the sink idempotent,
# which matters because onestep delivers at-least-once.
db = MySQLConnector(MYSQL_DSN)
sink = db.table_sink(table=TARGET_TABLE, mode="upsert", keys=("event_id",))

# Source: one synthetic event every 5 seconds.
source = IntervalSource.every(
    seconds=INTERVAL_SECONDS,
    immediate=True,
    overlap="skip",
    payload={"kind": "synthetic"},
)


def _run_docker(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["docker", *args],
            check=check,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("Docker is required for the default MySQL demo. Install Docker or set MYSQL_DSN.") from exc
    except subprocess.CalledProcessError as exc:
        output = (exc.stderr or exc.stdout or "").strip()
        raise RuntimeError(f"docker {' '.join(args)} failed: {output}") from exc


def _docker_container_running(name: str) -> bool:
    result = _run_docker(["inspect", "-f", "{{.State.Running}}", name], check=False)
    return result.returncode == 0 and result.stdout.strip() == "true"


def _docker_container_exists(name: str) -> bool:
    result = _run_docker(["inspect", name], check=False)
    return result.returncode == 0


def _ensure_mysql_container() -> None:
    if _docker_container_running(MYSQL_CONTAINER):
        print(f"MySQL Docker container already running: {MYSQL_CONTAINER}")
        return
    if _docker_container_exists(MYSQL_CONTAINER):
        print(f"Starting existing MySQL Docker container: {MYSQL_CONTAINER}")
        _run_docker(["start", MYSQL_CONTAINER])
        return

    print(f"Creating MySQL Docker container: {MYSQL_CONTAINER} ({MYSQL_IMAGE})")
    _run_docker(
        [
            "run",
            "-d",
            "--name",
            MYSQL_CONTAINER,
            "-e",
            f"MYSQL_ROOT_PASSWORD={MYSQL_PASSWORD}",
            "-e",
            "MYSQL_ROOT_HOST=%",
            "-e",
            f"MYSQL_DATABASE={MYSQL_DATABASE}",
            "-p",
            f"{MYSQL_HOST}:{MYSQL_PORT}:3306",
            MYSQL_IMAGE,
            "--mysql-native-password=ON",
        ]
    )


def _wait_for_mysql_container() -> None:
    deadline = time.monotonic() + MYSQL_WAIT_TIMEOUT_S
    last_output = ""
    while time.monotonic() < deadline:
        result = _run_docker(
            [
                "exec",
                MYSQL_CONTAINER,
                "mysqladmin",
                "ping",
                "-h",
                "127.0.0.1",
                "-uroot",
                f"-p{MYSQL_PASSWORD}",
            ],
            check=False,
        )
        if result.returncode == 0:
            return
        last_output = (result.stderr or result.stdout or "").strip()
        time.sleep(1)
    raise RuntimeError(f"Timed out waiting for Docker MySQL container {MYSQL_CONTAINER}: {last_output}")


def _sql_literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


def _ensure_pymysql_auth() -> None:
    password = _sql_literal(MYSQL_PASSWORD)
    sql = (
        f"ALTER USER 'root'@'%' IDENTIFIED WITH mysql_native_password BY {password}; "
        f"ALTER USER 'root'@'localhost' IDENTIFIED WITH mysql_native_password BY {password}; "
        "FLUSH PRIVILEGES;"
    )
    result = _run_docker(
        ["exec", MYSQL_CONTAINER, "mysql", "-uroot", f"-p{MYSQL_PASSWORD}", "-e", sql],
        check=False,
    )
    if result.returncode != 0:
        output = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(
            "Could not configure Docker MySQL for PyMySQL password auth. "
            f"Remove the container with `docker rm -f {MYSQL_CONTAINER}` and rerun. "
            f"Details: {output}"
        )


def _wait_for_mysql() -> None:
    deadline = time.monotonic() + MYSQL_WAIT_TIMEOUT_S
    last_error: BaseException | None = None
    while time.monotonic() < deadline:
        try:
            with db.engine.connect() as conn:
                conn.execute(sa.text("SELECT 1"))
            return
        except Exception as exc:  # pragma: no cover - depends on local Docker timing
            last_error = exc
            time.sleep(1)
    raise RuntimeError(f"Timed out waiting for MySQL at {_redact_dsn(MYSQL_DSN)}: {last_error}") from last_error


def _ensure_target_table() -> None:
    metadata = sa.MetaData()
    sa.Table(
        TARGET_TABLE,
        metadata,
        sa.Column("event_id", sa.String(16), primary_key=True),
        sa.Column("sequence", sa.BigInteger, nullable=True),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("scheduled_at", sa.String(64), nullable=True),
        sa.Column("processed_at", sa.String(64), nullable=False),
    )
    metadata.create_all(db.engine)


def _redact_dsn(dsn: str) -> str:
    try:
        return sa.engine.make_url(dsn).render_as_string(hide_password=True)
    except Exception:
        return "<invalid MYSQL_DSN>"


async def _prepare_mysql() -> None:
    def prepare_sync() -> None:
        if START_MYSQL:
            _ensure_mysql_container()
            _wait_for_mysql_container()
            _ensure_pymysql_auth()
        _wait_for_mysql()
        _ensure_target_table()

    await asyncio.to_thread(prepare_sync)


@app.on_startup
async def prepare_mysql(_: OneStepApp) -> None:
    await _prepare_mysql()


reporter: ControlPlaneReporter | None = None
if CONTROL_PLANE_ENABLED:
    # Attach the control-plane reporter so this worker shows up in the plane.
    reporter = ControlPlaneReporter(ControlPlaneReporterConfig.from_env(app_name=app.name))
    reporter.attach(app)


@app.on_startup
async def announce(_: OneStepApp) -> None:
    print(f"Service: {app.name}")
    print(f"OneStep version: {ONESTEP_VERSION}")
    if reporter is not None:
        print(f"Instance ID: {reporter.config.instance_id}")
    else:
        print("Control plane: disabled (set ONESTEP_CONTROL_PLANE_URL and ONESTEP_CONTROL_PLANE_TOKEN to enable)")
    print(f"MySQL DSN: {_redact_dsn(MYSQL_DSN)}")
    print(f"MySQL Docker: {'enabled' if START_MYSQL else 'disabled'}")
    if START_MYSQL:
        print(f"MySQL container: {MYSQL_CONTAINER}")
    print(f"Target table: {TARGET_TABLE} (upsert on event_id)")
    print(f"Interval: every {INTERVAL_SECONDS}s")
    print("Press Ctrl-C to stop.")


@app.task(
    description="Generate a synthetic event every few seconds, enrich it, and upsert into MySQL.",
    source=source,
    emit=sink,
    concurrency=1,
    retry=MaxAttempts(max_attempts=3, delay_s=1.0),
    timeout_s=10.0,
)
async def produce_and_store(ctx, item):
    """Build a deterministic row from the tick payload and hand it to the sink.

    The sink receives the returned dict; `event_id` is derived from the tick
    sequence so a replay upserts the same row instead of duplicating it.
    """
    sequence = ctx.current.meta.get("sequence")
    scheduled_at = ctx.current.meta.get("scheduled_at")
    kind = item.get("kind", "synthetic") if isinstance(item, dict) else "synthetic"

    seed = f"{kind}-{sequence}"
    event_id = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]
    processed_at = datetime.now(timezone.utc).isoformat()

    row = {
        "event_id": event_id,
        "sequence": sequence,
        "kind": kind,
        "scheduled_at": scheduled_at,
        "processed_at": processed_at,
    }
    print(f"upserting {TARGET_TABLE}: {row}")
    return row


if __name__ == "__main__":
    app.run()
