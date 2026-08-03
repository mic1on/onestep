---
title: Custom Source/Sink | Broker
outline: deep
---

# Custom Source/Sink

当内置连接器不能覆盖你的系统时，可以直接实现 `Source`、`Sink` 和 `Delivery`。

## 接口约定

```python
from onestep import Delivery, Envelope, Sink, Source


class MyDelivery(Delivery):
    def __init__(self, body, *, meta=None):
        super().__init__(Envelope(body=body, meta=dict(meta or {})))

    async def ack(self):
        # 任务成功后调用
        return None

    async def retry(self, *, delay_s: float | None = None):
        # 任务需要重试时调用
        return None

    async def fail(self, exc: Exception | None = None):
        # 任务最终失败后调用
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

`Source.fetch(limit)` 应该返回 0 到 `limit` 条消息。没有消息时返回空列表，运行时会按 `poll_interval_s` 继续轮询。

## 示例：文件输入输出

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

## 连接管理

如果连接器需要网络连接，实现 `open()` 和 `close()`：

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

运行时会在 `app.serve()` 启动时打开资源，并在关闭时反向关闭资源。

## 注意事项

- `ack()` 应该只在消息已经成功处理后确认外部系统。
- `retry()` 应尽量保留原消息，供下次 `fetch()` 重新取到。
- `fail()` 用于终端失败；如任务配置了 `dead_letter`，运行时会先写入死信 Sink，再调用 `fail()`。
- 连接错误可抛出普通异常；需要更细粒度降级时，可使用 `ConnectorOperationError`。
