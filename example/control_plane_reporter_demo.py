from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import quote

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from onestep import (
    ControlPlaneReporter,
    ControlPlaneReporterConfig,
    IntervalSource,
    MaxAttempts,
    MemoryQueue,
    OneStepApp,
    build_control_plane_http_base_url,
    build_control_plane_ws_url,
)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

SERVICE_NAME = os.getenv("ONESTEP_SERVICE_NAME", "control-plane-demo")
CP_BASE_URL = os.getenv("ONESTEP_CONTROL_PLANE_URL") or os.getenv("ONESTEP_CONTROL_URL", "")
ENVIRONMENT = os.getenv("ONESTEP_CONTROL_PLANE_ENVIRONMENT") or os.getenv("ONESTEP_ENV", "dev")
INTERVAL_SECONDS = int(os.getenv("CONTROL_PLANE_DEMO_INTERVAL_S", "3"))
WORKER_TIMEOUT_S = float(os.getenv("CONTROL_PLANE_DEMO_WORKER_TIMEOUT_S", "0.75"))
SLOW_JOB_SLEEP_S = float(os.getenv("CONTROL_PLANE_DEMO_SLOW_JOB_SLEEP_S", "2.0"))
JOB_BEHAVIOR_SEQUENCE = ("ok", "retry_once", "fail", "slow")

jobs = MemoryQueue("control-plane-demo.jobs", maxsize=100, batch_size=50, poll_interval_s=0.05)
results = MemoryQueue("control-plane-demo.results", maxsize=100, batch_size=50, poll_interval_s=0.05)
dead_letter = MemoryQueue("control-plane-demo.dead-letter", maxsize=100, batch_size=50, poll_interval_s=0.05)

app = OneStepApp(SERVICE_NAME)
reporter = ControlPlaneReporter(ControlPlaneReporterConfig.from_env(app_name=app.name))
reporter.attach(app)

_job_counter = {"value": 0}
_retried_once: set[int] = set()
_completed_counts: Counter[str] = Counter()
_dead_letter_counts: Counter[str] = Counter()


def _build_demo_job(job_id: int) -> dict[str, object]:
    behavior = JOB_BEHAVIOR_SEQUENCE[(job_id - 1) % len(JOB_BEHAVIOR_SEQUENCE)]
    return {
        "job_id": job_id,
        "behavior": behavior,
        "payload": {
            "job_number": job_id,
            "note": f"demo-{behavior}",
        },
    }


def _build_control_plane_urls(
    *,
    base_url: str,
    service_name: str,
    environment: str,
    instance_id: str,
) -> dict[str, str]:
    service_path = quote(service_name, safe="")
    return {
        "dashboard": f"{base_url}/services/{service_path}?environment={environment}",
        "tasks": f"{base_url}/services/{service_path}?environment={environment}&tab=tasks",
        "instances": f"{base_url}/services/{service_path}?environment={environment}&tab=instances",
        "commands": f"{base_url}/services/{service_path}?environment={environment}&tab=commands",
        "instance_detail": (
            f"{base_url}/services/{service_path}/instances/{instance_id}"
            f"?environment={environment}&lookback_minutes=60"
        ),
    }


@app.on_startup
async def announce(_: OneStepApp) -> None:
    print(f"Service name: {app.name}")
    print(f"Environment: {ENVIRONMENT}")
    print(f"Instance ID: {reporter.config.instance_id}")
    print(f"State dir: {reporter.config.state_dir}")
    print(f"Interval: {INTERVAL_SECONDS}s")
    print(f"Worker timeout: {WORKER_TIMEOUT_S}s")
    print(f"Slow-job sleep: {SLOW_JOB_SLEEP_S}s")
    print("Behavior cycle: ok -> retry_once -> fail(dead-letter) -> slow(timeout + dead-letter)")
    if CP_BASE_URL:
        base_url = build_control_plane_http_base_url(CP_BASE_URL)
        print(f"Control Plane HTTP base: {base_url}")
        print(f"Control Plane WS endpoint: {build_control_plane_ws_url(CP_BASE_URL)}")
        console_urls = _build_control_plane_urls(
            base_url=base_url,
            service_name=app.name,
            environment=ENVIRONMENT,
            instance_id=str(reporter.config.instance_id),
        )
        print("Control Plane views:")
        for label, url in console_urls.items():
            print(f"  {label}: {url}")
    print("Press Ctrl-C to stop.")


@app.on_shutdown
async def print_summary(_: OneStepApp) -> None:
    summary = {
        "completed": dict(_completed_counts),
        "dead_lettered": dict(_dead_letter_counts),
    }
    print("Demo summary:")
    print(json.dumps(summary, indent=2, sort_keys=True))


@app.task(
    description="Generate demo jobs that cycle through success, retry, failure, and timeout cases.",
    source=IntervalSource.every(seconds=INTERVAL_SECONDS, immediate=True, overlap="skip"),
    emit=jobs,
)
async def enqueue_job(ctx, _) -> dict[str, object]:
    _job_counter["value"] += 1
    return _build_demo_job(_job_counter["value"])


@app.task(
    description="Process demo jobs and intentionally trigger retries, failures, and timeouts.",
    source=jobs,
    emit=results,
    dead_letter=dead_letter,
    concurrency=4,
    timeout_s=WORKER_TIMEOUT_S,
    retry=MaxAttempts(max_attempts=2, delay_s=1.0),
)
async def process_job(ctx, item) -> dict[str, object]:
    job_id = int(item["job_id"])
    behavior = str(item["behavior"])
    if behavior == "retry_once" and job_id not in _retried_once:
        _retried_once.add(job_id)
        raise RuntimeError(f"demo retry_once for job {job_id}")
    if behavior == "fail":
        raise RuntimeError(f"demo terminal failure for job {job_id}")
    if behavior == "slow":
        await asyncio.sleep(SLOW_JOB_SLEEP_S)
    return {
        "job_id": job_id,
        "behavior": behavior,
        "status": "processed",
    }


@app.task(
    description="Consume successful demo results so local stdout matches the control-plane metrics view.",
    source=results,
    concurrency=4,
)
async def consume_result(ctx, item) -> None:
    behavior = str(item["behavior"])
    _completed_counts[behavior] += 1
    print(f"completed job={item['job_id']} behavior={behavior} status={item['status']}")


@app.task(
    description="Inspect dead-letter payloads produced by terminal failures and timeouts.",
    source=dead_letter,
    concurrency=2,
)
async def inspect_dead_letter(ctx, item: dict[str, Any]) -> None:
    payload = item.get("payload", {})
    failure = item.get("failure", {})
    behavior = str(payload.get("behavior", "unknown"))
    _dead_letter_counts[behavior] += 1
    print(
        "dead-letter job="
        f"{payload.get('job_id')} behavior={behavior} "
        f"failure={failure.get('kind')} message={failure.get('message')}"
    )


if __name__ == "__main__":
    app.run()
