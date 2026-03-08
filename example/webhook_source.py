from onestep import MemoryQueue, OneStepApp, WebhookSource

app = OneStepApp("webhook-demo")
jobs = MemoryQueue("jobs")


@app.task(
    source=WebhookSource(
        path="/webhooks/github",
        methods=("POST",),
        host="127.0.0.1",
        port=8080,
    ),
    emit=jobs,
)
async def ingest_github(ctx, event):
    print("received:", event["path"], event["headers"])
    return {
        "event": event["headers"].get("x-github-event"),
        "payload": event["body"],
    }
