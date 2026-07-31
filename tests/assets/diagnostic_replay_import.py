from __future__ import annotations

import os
import time
from pathlib import Path

from onestep import OneStepApp

marker = os.environ.get("ONESTEP_REPLAY_IMPORT_MARKER")
if marker:
    with Path(marker).open("a", encoding="utf-8") as handle:
        handle.write("imported\n")

delay_s = float(os.environ.get("ONESTEP_REPLAY_IMPORT_DELAY_S", "0"))
if delay_s:
    time.sleep(delay_s)

app = OneStepApp("diagnostic-replay-import")


@app.task()
async def replay(ctx, payload):
    return payload
