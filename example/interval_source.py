from onestep import IntervalSource, OneStepApp

app = OneStepApp("interval-demo")


@app.task(
    source=IntervalSource.every(
        seconds=5,
        immediate=True,
        overlap="skip",
        payload={"job": "refresh-cache"},
    )
)
async def refresh_cache(ctx, item):
    sequence = ctx.current.meta["sequence"]
    scheduled_at = ctx.current.meta["scheduled_at"]
    print("tick:", sequence, "scheduled_at:", scheduled_at, "payload:", item)
    if sequence >= 2:
        ctx.app.request_shutdown()
