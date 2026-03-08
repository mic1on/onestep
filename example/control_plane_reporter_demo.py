from __future__ import annotations

import logging
import os
from urllib.parse import quote

from onestep import (
    ControlPlaneReporter,
    ControlPlaneReporterConfig,
    IntervalSource,
    MaxAttempts,
    MemoryQueue,
    OneStepApp,
)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

SERVICE_NAME = os.getenv("ONESTEP_SERVICE_NAME", "control-plane-demo")
CP_BASE_URL = os.getenv("ONESTEP_CONTROL_PLANE_URL") or os.getenv("ONESTEP_CONTROL_URL", "")
ENVIRONMENT = os.getenv("ONESTEP_CONTROL_PLANE_ENVIRONMENT") or os.getenv("ONESTEP_ENV", "dev")
INTERVAL_SECONDS = int(os.getenv("CONTROL_PLANE_DEMO_INTERVAL_S", "10"))

jobs = MemoryQueue("control-plane-demo.jobs", maxsize=100, batch_size=50, poll_interval_s=0.05)
results = MemoryQueue("control-plane-demo.results", maxsize=100, batch_size=50, poll_interval_s=0.05)

app = OneStepApp(SERVICE_NAME)
reporter = ControlPlaneReporter(ControlPlaneReporterConfig.from_env(app_name=app.name))
reporter.attach(app)

_job_counter = {"value": 0}
_failed_once: set[int] = set()


@app.on_startup
async def announce(_: OneStepApp) -> None:
    service_path = quote(app.name, safe="")
    print(f"Service name: {app.name}")
    print(f"Environment: {ENVIRONMENT}")
    print(f"Instance ID: {reporter.config.instance_id}")
    if CP_BASE_URL:
        print(f"Control Plane dashboard: {CP_BASE_URL}/api/v1/services/{service_path}/dashboard?environment={ENVIRONMENT}")
        print(f"Control Plane tasks: {CP_BASE_URL}/api/v1/services/{service_path}/tasks?environment={ENVIRONMENT}")
    print("Press Ctrl-C to stop.")


@app.task(
    source=IntervalSource.every(seconds=INTERVAL_SECONDS, immediate=True, overlap="skip"),
    emit=jobs,
)
async def enqueue_job(ctx, _) -> dict[str, object]:
    _job_counter["value"] += 1
    job_id = _job_counter["value"]
    return {
        "job_id": job_id,
        "fail_once": job_id % 2 == 1,
    }


@app.task(
    source=jobs,
    emit=results,
    concurrency=4,
    timeout_s=10.0,
    retry=MaxAttempts(max_attempts=2, delay_s=1.0),
)
async def process_job(ctx, item) -> dict[str, object]:
    job_id = int(item["job_id"])
    if bool(item["fail_once"]) and job_id not in _failed_once:
        _failed_once.add(job_id)
        raise RuntimeError(f"demo retry for job {job_id}")
    return {
        "job_id": job_id,
        "status": "processed",
    }


@app.task(source=results, concurrency=4)
async def consume_result(ctx, item) -> None:
    print(f"processed job {item['job_id']} status={item['status']}")


if __name__ == "__main__":
    app.run()
