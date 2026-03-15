import asyncio

from onestep import MemoryQueue, OneStepApp

app = OneStepApp("memory-demo")
source = MemoryQueue("incoming")
sink = MemoryQueue("processed")


@app.task(source=source, emit=sink, concurrency=2)
async def double(ctx, item):
    print("received:", item)
    ctx.app.request_shutdown()
    return {"value": item["value"] * 2}


async def main() -> None:
    await source.publish({"value": 21})
    await app.serve()
    deliveries = await sink.fetch(1)
    for delivery in deliveries:
        print("processed:", delivery.payload)


if __name__ == "__main__":
    asyncio.run(main())
