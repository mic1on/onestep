from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

from onestep import OneStepApp
from onestep.connectors.base import Sink
from onestep.envelope import Envelope
from onestep.task import TaskHooks


async def yaml_handler(ctx, payload):
    return {"value": payload["value"] + 1}


app = OneStepApp("diagnostic-success")


@app.task()
async def success(ctx, payload):
    return {"value": payload["value"] + 1}


blocking_app = OneStepApp("diagnostic-blocking")


@blocking_app.task()
def block(ctx, payload):
    while True:
        time.sleep(1)


hook_blocking_app = OneStepApp("diagnostic-hook-blocking")


def blocking_before(ctx, payload):
    while True:
        time.sleep(1)


@hook_blocking_app.task(hooks=TaskHooks(before=(blocking_before,)))
async def blocked_by_hook(ctx, payload):
    return payload


async_cancel_app = OneStepApp("diagnostic-async-cancel")


@async_cancel_app.task()
async def wait_forever(ctx, payload):
    await asyncio.Event().wait()


output_app = OneStepApp("diagnostic-output")


@output_app.task()
async def write_output(ctx, payload):
    print("child stdout marker")
    print("child stderr marker", file=sys.stderr)
    return None


class BlockingSink(Sink):
    async def send(self, envelope: Envelope) -> None:
        marker = os.environ.get("ONESTEP_DIAGNOSTIC_SEND_MARKER")
        if marker:
            Path(marker).write_text("entered", encoding="utf-8")
        while True:
            time.sleep(1)


send_blocking_app = OneStepApp("diagnostic-send-blocking")


@send_blocking_app.task(emit=BlockingSink("blocking"))
async def block_during_send(ctx, payload):
    return {"sent": True}


exit_app = OneStepApp("diagnostic-exit")


@exit_app.task()
async def exit_without_final(ctx, payload):
    os._exit(17)
