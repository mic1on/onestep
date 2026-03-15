from onestep import RabbitMQConnector, OneStepApp

app = OneStepApp("rabbitmq-demo")
rmq = RabbitMQConnector("amqp://guest:guest@localhost/")
queue = rmq.queue(
    "incoming_jobs",
    exchange="jobs.events",
    routing_key="jobs.created",
    prefetch=50,
)
processed = rmq.queue(
    "processed_jobs",
    exchange="jobs.events",
    routing_key="jobs.done",
)


@app.task(source=queue, emit=processed, concurrency=8)
async def process_job(ctx, item):
    return {"job": item["job"], "status": "done"}
