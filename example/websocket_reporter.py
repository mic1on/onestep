"""
WebSocket Reporter Example.

This example demonstrates how to use WebSocket-based reporter
for bidirectional communication with Control Plane.

Run with:
    PYTHONPATH=src python example/websocket_reporter.py

Prerequisites:
    pip install 'onestep[websocket]'
"""

import asyncio
import os

from onestep import (
    OneStepApp,
    MemoryQueue,
    WebSocketReporter,
    WebSocketReporterConfig,
)

# Configure WebSocket reporter
config = WebSocketReporterConfig(
    ws_url=os.getenv("CONTROL_PLANE_WS_URL", "ws://localhost:8080/ws/agents"),
    token=os.getenv("CONTROL_PLANE_TOKEN", "test-token"),
    environment="dev",
    heartbeat_interval_s=10.0,
    reconnect_delay_s=1.0,
)

# Create reporter
reporter = WebSocketReporter(config)

# Create app with reporter
app = OneStepApp("websocket-demo", reporter=reporter)

# Simple in-memory queue for demo
jobs = MemoryQueue("jobs")


@app.task(source=jobs, concurrency=2)
async def process_job(ctx, item):
    """Process jobs - can be controlled via Control Plane."""
    print(f"Processing: {item}")
    await asyncio.sleep(1)
    return {"result": f"processed-{item}"}


@app.on_startup
async def publish_test_jobs(app):
    """Publish some test jobs."""
    print("Publishing test jobs...")
    for i in range(5):
        await jobs.publish({"job_id": i, "data": f"test-data-{i}"})


if __name__ == "__main__":
    print("WebSocket Reporter Demo")
    print(f"Connecting to: {config.ws_url}")
    print(f"Instance ID: {config.instance_id}")
    print()
    print("Control Plane can send commands:")
    print("  - pause: Pause all tasks")
    print("  - resume: Resume all tasks")
    print("  - scale: Adjust concurrency")
    print("  - shutdown: Graceful shutdown")
    print()
    app.run()