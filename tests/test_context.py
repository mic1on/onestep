import asyncio

import pytest

from onestep import MemoryQueue, OneStepApp


def test_update_current_row_requires_mutable_row_delivery() -> None:
    handled: list[bool] = []

    async def scenario() -> None:
        app = OneStepApp("context-update-current-row")
        source = MemoryQueue("incoming", batch_size=1, poll_interval_s=0.01)

        @app.task(source=source, concurrency=1)
        async def process(ctx, row):
            with pytest.raises(RuntimeError, match="update_current_row\\(\\)"):
                await ctx.update_current_row({"status": "done"})
            handled.append(True)
            ctx.app.request_shutdown()

        await source.publish({"id": 1})
        await app.serve()

    asyncio.run(scenario())

    assert handled == [True]
