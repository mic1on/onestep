from __future__ import annotations

import json
from typing import Any

from .transforms import normalize_user


async def normalize_users(ctx, payload: dict[str, Any], *, phone_country_code: str) -> dict[str, Any] | None:
    row = normalize_user(
        payload,
        region=str(ctx.task_config.get("region", "unknown")),
        phone_country_code=phone_country_code,
    )
    if ctx.task_config.get("dry_run"):
        print(
            json.dumps(
                {
                    "task": ctx.task.name,
                    "dry_run": True,
                    "row": row,
                },
                ensure_ascii=False,
            )
        )
        return None
    return row


async def print_row(ctx, payload: dict[str, Any]) -> None:
    seen = int(await ctx.state.get("seen", 0)) + 1
    await ctx.state.set("seen", seen)
    print(
        json.dumps(
            {
                "task": ctx.task.name,
                "row": payload,
                "seen": seen,
            },
            ensure_ascii=False,
        )
    )
    if seen >= int(ctx.config["expected_rows"]):
        ctx.app.request_shutdown()
