import json
import logging
import os

from onestep import (
    InMemoryMetrics,
    MemoryQueue,
    NoRetry,
    OneStepApp,
    StructuredEventLogger,
    WebhookResponse,
    WebhookSource,
)


HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8090"))


class EventJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in (
            "event_kind",
            "app_name",
            "task_name",
            "source_name",
            "attempts",
            "duration_s",
            "failure_kind",
            "failure_exception_type",
            "failure_message",
            "task_event_meta",
        ):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging() -> logging.Logger:
    logger = logging.getLogger("showcase.events")
    logger.handlers = []
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    handler = logging.StreamHandler()
    handler.setFormatter(EventJsonFormatter())
    logger.addHandler(handler)
    return logger


jobs = MemoryQueue("showcase.jobs", poll_interval_s=0.01)
dead_letter = MemoryQueue("showcase.dead", poll_interval_s=0.01)
metrics = InMemoryMetrics()
app = OneStepApp("runtime-showcase")
app.on_event(metrics)
app.on_event(StructuredEventLogger(logger=configure_logging()))


@app.on_startup
def print_instructions(app: OneStepApp) -> None:
    print(f"Webhook endpoint: http://{HOST}:{PORT}/demo/webhook")
    print("Try:")
    print(
        f"""curl -X POST http://{HOST}:{PORT}/demo/webhook \\
  -H 'Content-Type: application/json' \\
  -d '{{"action":"ok","value":21}}'"""
    )
    print(
        f"""curl -X POST http://{HOST}:{PORT}/demo/webhook \\
  -H 'Content-Type: application/json' \\
  -d '{{"action":"fail","value":21}}'"""
    )
    print(
        f"""curl -X POST http://{HOST}:{PORT}/demo/webhook \\
  -H 'Content-Type: application/json' \\
  -d '{{"action":"slow","value":21}}'"""
    )
    print("Press Ctrl-C to stop.")


@app.on_shutdown
def print_metrics() -> None:
    print("Metrics snapshot:")
    print(json.dumps(metrics.snapshot(), indent=2, ensure_ascii=False))


@app.task(
    source=WebhookSource(
        path="/demo/webhook",
        methods=("POST",),
        host=HOST,
        port=PORT,
        response=WebhookResponse(status_code=202, body={"accepted": True}),
    ),
    emit=jobs,
)
async def ingest_webhook(ctx, event):
    return event["body"]


@app.task(source=jobs, retry=NoRetry(), dead_letter=dead_letter, timeout_s=0.5)
async def process_job(ctx, item):
    action = item.get("action", "ok")
    if action == "slow":
        import asyncio

        await asyncio.sleep(2.0)
        return None
    if action == "fail":
        raise RuntimeError("requested failure")
    print(f"processed payload: {item}")
    return None


@app.task(source=dead_letter)
async def inspect_dead_letter(ctx, item):
    print("dead-letter received:")
    print(json.dumps(item, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    app.run()
