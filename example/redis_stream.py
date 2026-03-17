"""
Redis Streams example for onestep.

Requires: pip install 'onestep[redis]'

Run with:
    PYTHONPATH=src python example/redis_stream.py
    
Or with YAML:
    PYTHONPATH=src onestep run example/redis_stream.yaml
"""
import asyncio
import os

from onestep import OneStepApp, RedisConnector

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

app = OneStepApp("redis-demo")
redis = RedisConnector(REDIS_URL)

# Source and sink using Redis Streams
incoming = redis.stream(
    "onestep:jobs",
    group="workers",
    batch_size=10,
    poll_interval_s=0.5,
)
processed = redis.stream("onestep:processed")


# Handler function that can be referenced from YAML
async def handle_job(ctx, item):
    """Process a job - can be used as YAML handler ref."""
    print(f"Processing: {item}")
    return {"job": item.get("job"), "status": "done", "processed_at": ctx.current.meta.get("received_at")}


@app.task(source=incoming, emit=processed, concurrency=4)
async def process_job(ctx, item):
    """Process jobs from Redis Streams."""
    return await handle_job(ctx, item)


@app.on_startup
async def publish_test_jobs(app):
    """Publish some test jobs on startup."""
    print(f"Publishing test jobs to {incoming.name}...")
    for i in range(5):
        await incoming.publish({"job": f"test-job-{i}", "value": i * 10})
    print("Published 5 test jobs")


@app.on_shutdown
async def show_results(app):
    """Show processed results."""
    print(f"\nProcessed stream length: {await processed._redis.xlen(processed.name)}")


if __name__ == "__main__":
    print("Redis Streams Demo")
    print(f"REDIS_URL: {REDIS_URL}")
    print()
    app.run()