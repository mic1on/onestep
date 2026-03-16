import asyncio

from onestep import MemoryQueue, OneStepApp


def test_memory_queue_pipeline() -> None:
    async def scenario() -> None:
        source = MemoryQueue("incoming")
        sink = MemoryQueue("processed")
        app = OneStepApp("memory-pipeline")

        @app.task(source=source, emit=sink, concurrency=2)
        async def double(ctx, item):
            ctx.app.request_shutdown()
            return {"value": item["value"] * 2}

        await source.publish({"value": 21})
        await app.serve()

        deliveries = await sink.fetch(1)
        assert len(deliveries) == 1
        assert deliveries[0].payload == {"value": 42}

    asyncio.run(scenario())
