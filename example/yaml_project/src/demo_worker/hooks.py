from __future__ import annotations

import json

_SAMPLE_USERS = [
    {
        "id": 1,
        "name": " Alice ",
        "email": "ALICE@example.com ",
        "phone": "13800138000",
    },
    {
        "id": 2,
        "name": "Bob",
        "email": " bob@example.com",
        "phone": "13900139000",
    },
]


async def seed_demo_data(app) -> None:
    print(
        json.dumps(
            {
                "hook": "startup",
                "message": "seeding demo users",
                "rows": len(_SAMPLE_USERS),
            }
        )
    )
    for row in _SAMPLE_USERS:
        await app.resources["incoming_users"].publish(row)


def before_normalize(ctx, payload) -> None:
    print(
        json.dumps(
            {
                "hook": "before_normalize",
                "task": ctx.task.name,
                "user_id": payload["id"],
            }
        )
    )


def after_normalize(ctx, payload, result) -> None:
    print(
        json.dumps(
            {
                "hook": "after_normalize",
                "task": ctx.task.name,
                "user_id": payload["id"],
                "email": result["email"],
            }
        )
    )


def on_shutdown(app) -> None:
    print(
        json.dumps(
            {
                "hook": "shutdown",
                "app": app.name,
            }
        )
    )
