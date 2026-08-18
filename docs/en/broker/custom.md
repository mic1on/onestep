---
title: Custom Source/Sink | Broker
outline: deep
---

# Custom Source/Sink

When built-in connectors don't cover your system, implement `Source`, `Sink`, and `Delivery` directly.

## Interface Contract

```python
from onestep import Delivery, Envelope, Sink, Source


class MyDelivery(Delivery):
    def __init__(self, body, *, meta=None):
        super().__init__(Envelope(body=body, meta=dict(meta or {})))

    async def ack(self):
        # Called after task success
        return None

    async def retry(self, *, delay_s: float | None = None):
        # Called when task needs retry
        return None

    async def fail(self, exc: Exception | None = None):
        # Called after final task failure
        return None


class MySource(Source):
    batch_size = 100
    poll_interval_s = 1.0

    def __init__(self):
        super().__init__("my-source")

    async def fetch(self, limit: int) -> list[Delivery]:
        records = await load_records(limit)
        return [MyDelivery(record) for record in records]


class MySink(Sink):
    def __init__(self):
        super().__init__("my-sink")

    async def send(self, envelope: Envelope) -> None:
        await publish_record(envelope.body, meta=envelope.meta)
```

`Source.fetch(limit)` should return 0 to `limit` messages. Return an empty list when no messages are available; the runtime continues polling at `poll_interval_s`.

## Example: File Input/Output

```python
import json
from pathlib import Path

from onestep import Delivery, Envelope, OneStepApp, Sink, Source


class FileDelivery(Delivery):
    def __init__(self, path: Path, body):
        super().__init__(Envelope(body=body, meta={"file": str(path)}))
        self.path = path

    async def ack(self):
        self.path.unlink(missing_ok=True)

    async def retry(self, *, delay_s: float | None = None):
        return None

    async def fail(self, exc: Exception | None = None):
        failed_path = self.path.with_suffix(self.path.suffix + ".failed")
        self.path.rename(failed_path)


class FileSource(Source):
    def __init__(self, directory: str, pattern: str = "*.json"):
        super().__init__("file-source")
        self.directory = Path(directory)
        self.pattern = pattern

    async def fetch(self, limit: int) -> list[Delivery]:
        deliveries: list[Delivery] = []
        for path in sorted(self.directory.glob(self.pattern))[:limit]:
            deliveries.append(FileDelivery(path, json.loads(path.read_text())))
        return deliveries


class FileSink(Sink):
    def __init__(self, directory: str):
        super().__init__("file-sink")
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.counter = 0

    async def send(self, envelope: Envelope) -> None:
        self.counter += 1
        path = self.directory / f"result-{self.counter}.json"
        path.write_text(json.dumps(envelope.body, ensure_ascii=False, indent=2))


app = OneStepApp("file-demo")
source = FileSource("incoming")
sink = FileSink("processed")


@app.task(source=source, emit=sink)
async def process(ctx, item):
    return {"processed": item}
```

## Connection Management

If the connector needs a network connection, implement `open()` and `close()`:

```python
class NetworkSink(Sink):
    def __init__(self, dsn: str):
        super().__init__("network-sink")
        self.dsn = dsn
        self.client = None

    async def open(self):
        self.client = await connect(self.dsn)

    async def send(self, envelope: Envelope):
        await self.client.publish(envelope.body)

    async def close(self):
        if self.client is not None:
            await self.client.close()
```

The runtime opens resources when `app.serve()` starts and closes them in reverse order on shutdown.

## Notes

- `ack()` should only acknowledge the external system after the message has been successfully processed.
- `retry()` should preserve the original message as much as possible so it can be fetched again on the next `fetch()`.
- `fail()` is for terminal failure; if the task has a `dead_letter` configured, the runtime writes to the dead letter Sink first, then calls `fail()`.
- Connection errors can be raised as normal exceptions; for finer-grained degradation, use `ConnectorOperationError`.
