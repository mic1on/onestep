from __future__ import annotations

import json

from worker_image_demo import MARKER


async def run_once(ctx, payload):
    print(json.dumps({"marker": MARKER, "payload": payload}, ensure_ascii=False))
    ctx.app.request_shutdown()
