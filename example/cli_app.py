import json
import logging
import os

from onestep import IntervalSource, OneStepApp, StructuredEventLogger


def _build_logger() -> logging.Logger:
    logger = logging.getLogger("cli-app.events")
    if logger.handlers:
        return logger
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(event_kind)s app=%(app_name)s task=%(task_name)s message=%(message)s"
        )
    )
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


SYNC_INTERVAL_SECONDS = int(os.getenv("SYNC_INTERVAL_SECONDS", "3600"))
SERVICE_NAME = os.getenv("SERVICE_NAME", "demo-sync")

app = OneStepApp(
    "cli-demo",
    config={
        "service_name": SERVICE_NAME,
    },
)
app.on_event(StructuredEventLogger(logger=_build_logger()))


@app.task(
    source=IntervalSource.every(
        seconds=SYNC_INTERVAL_SECONDS,
        immediate=True,
        overlap="skip",
        payload={"job": "sync-users"},
    )
)
async def sync_users(ctx, payload):
    print(
        json.dumps(
            {
                "service": ctx.config["service_name"],
                "task": "sync_users",
                "payload": payload,
            },
            ensure_ascii=False,
        )
    )
