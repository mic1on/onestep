from onestep import OneStepApp, SQSConnector

app = OneStepApp("sqs-demo")
sqs = SQSConnector(region_name="ap-southeast-1")
queue = sqs.queue(
    "https://sqs.ap-southeast-1.amazonaws.com/123456789/jobs.fifo",
    message_group_id="workers",
    delete_batch_size=10,
    delete_flush_interval_s=0.5,
    heartbeat_interval_s=15,
    heartbeat_visibility_timeout=60,
)
out = sqs.queue(
    "https://sqs.ap-southeast-1.amazonaws.com/123456789/processed.fifo",
    message_group_id="workers",
)


@app.task(source=queue, emit=out, concurrency=16)
async def process_job(ctx, item):
    return {"job": item["job"], "status": "done"}
