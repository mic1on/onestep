"""Demo handler for the Prometheus monitoring example.

Mounted next to onestep.yaml (see docker-compose.yml) so
``ref: handlers:heartbeat`` resolves when the worker starts.
"""


async def heartbeat(ctx, payload):
    """A trivial task body: pretend to do work for ~50 ms."""
    import asyncio

    await asyncio.sleep(0.05)
    return {"beat": True}
