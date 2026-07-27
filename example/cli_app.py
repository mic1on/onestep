import logging
import os

from onestep import IntervalSource, OneStepApp


SYNC_INTERVAL_SECONDS = int(os.getenv("SYNC_INTERVAL_SECONDS", "3600"))
SERVICE_NAME = os.getenv("SERVICE_NAME", "demo-sync")
logger = logging.getLogger("onestep.cli_app")

app = OneStepApp(
    "cli-demo",
    config={
        "service_name": SERVICE_NAME,
    },
)


@app.task(
    source=IntervalSource.every(
        seconds=SYNC_INTERVAL_SECONDS,
        immediate=True,
        overlap="skip",
        payload={"job": "sync-users"},
    )
)
async def sync_users(ctx, payload):
    logger.info(
        "synced users service=%s payload=%r",
        ctx.config["service_name"],
        payload,
    )
