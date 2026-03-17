---
title: Custom Broker | Broker
outline: deep
---

# Custom Broker

实现自定义 Source 和 Sink，对接任意数据源。

## Source 接口

```python
from onestep import Source, Delivery
from typing import List


class MySource(Source):
    """自定义数据源"""
    
    def __init__(self, name: str = "my-source"):
        self.name = name
    
    async def fetch(self) -> List[Delivery]:
        """
        获取消息列表
        
        返回:
            List[Delivery]: 消息列表，空列表表示暂无消息
        """
        # 实现获取逻辑
        data = await self.get_data()
        
        # 转换为 Delivery 对象
        return [Delivery(body=item) for item in data]
    
    async def ack(self, delivery: Delivery):
        """确认消息已处理成功"""
        pass
    
    async def nack(self, delivery: Delivery):
        """确认消息处理失败"""
        pass
    
    async def close(self):
        """关闭资源"""
        pass
```

## Sink 接口

```python
from onestep import Sink


class MySink(Sink):
    """自定义输出"""
    
    def __init__(self, name: str = "my-sink"):
        self.name = name
    
    async def publish(self, body, meta=None):
        """
        发布消息
        
        参数:
            body: 消息体
            meta: 元数据（可选）
        """
        # 实现发布逻辑
        await self.send(body)
    
    async def close(self):
        """关闭资源"""
        pass
```

## 示例：Redis Stream

```python
import aioredis
from onestep import Source, Sink, Delivery
from typing import List


class RedisStreamSource(Source):
    def __init__(self, stream: str, group: str, consumer: str):
        self.stream = stream
        self.group = group
        self.consumer = consumer
        self.redis = None
    
    async def connect(self):
        self.redis = await aioredis.create_redis_pool("redis://localhost")
    
    async def fetch(self) -> List[Delivery]:
        if not self.redis:
            await self.connect()
        
        # 从 Stream 读取
        messages = await self.redis.xreadgroup(
            self.group,
            self.consumer,
            {self.stream: ">"},
            count=10,
            block=1000
        )
        
        if not messages:
            return []
        
        deliveries = []
        for stream_name, stream_messages in messages:
            for msg_id, fields in stream_messages:
                deliveries.append(
                    Delivery(
                        body=fields,
                        meta={"message_id": msg_id.decode()}
                    )
                )
        
        return deliveries
    
    async def ack(self, delivery: Delivery):
        msg_id = delivery.meta.get("message_id")
        if msg_id:
            await self.redis.xack(self.stream, self.group, msg_id)
    
    async def nack(self, delivery: Delivery):
        # Redis Stream 会自动重新投递未确认的消息
        pass
    
    async def close(self):
        if self.redis:
            self.redis.close()
            await self.redis.wait_closed()


class RedisStreamSink(Sink):
    def __init__(self, stream: str):
        self.stream = stream
        self.redis = None
    
    async def connect(self):
        self.redis = await aioredis.create_redis_pool("redis://localhost")
    
    async def publish(self, body, meta=None):
        if not self.redis:
            await self.connect()
        await self.redis.xadd(self.stream, body)
    
    async def close(self):
        if self.redis:
            self.redis.close()
            await self.redis.wait_closed()
```

## 示例：Kafka

```python
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from onestep import Source, Sink, Delivery
from typing import List


class KafkaSource(Source):
    def __init__(self, topic: str, bootstrap_servers: str):
        self.topic = topic
        self.bootstrap_servers = bootstrap_servers
        self.consumer = None
    
    async def connect(self):
        self.consumer = AIOKafkaConsumer(
            self.topic,
            bootstrap_servers=self.bootstrap_servers,
            group_id="onestep-group",
            auto_offset_reset="latest"
        )
        await self.consumer.start()
    
    async def fetch(self) -> List[Delivery]:
        if not self.consumer:
            await self.connect()
        
        deliveries = []
        async for msg in self.consumer.getmany(timeout_ms=1000):
            deliveries.append(
                Delivery(
                    body=msg.value,
                    meta={
                        "topic": msg.topic,
                        "partition": msg.partition,
                        "offset": msg.offset
                    }
                )
            )
        
        return deliveries
    
    async def ack(self, delivery: Delivery):
        # Kafka 消费者自动提交 offset
        pass
    
    async def close(self):
        if self.consumer:
            await self.consumer.stop()


class KafkaSink(Sink):
    def __init__(self, topic: str, bootstrap_servers: str):
        self.topic = topic
        self.bootstrap_servers = bootstrap_servers
        self.producer = None
    
    async def connect(self):
        self.producer = AIOKafkaProducer(
            bootstrap_servers=self.bootstrap_servers
        )
        await self.producer.start()
    
    async def publish(self, body, meta=None):
        if not self.producer:
            await self.connect()
        await self.producer.send_and_wait(
            self.topic,
            body.encode() if isinstance(body, str) else body
        )
    
    async def close(self):
        if self.producer:
            await self.producer.stop()
```

## 示例：文件系统

```python
import aiofiles
import json
from pathlib import Path
from onestep import Source, Sink, Delivery
from typing import List


class FileSource(Source):
    """从目录读取 JSON 文件作为消息"""
    
    def __init__(self, directory: str, pattern: str = "*.json"):
        self.directory = Path(directory)
        self.pattern = pattern
        self.processed = set()
    
    async def fetch(self) -> List[Delivery]:
        deliveries = []
        
        for file_path in self.directory.glob(self.pattern):
            if file_path.name in self.processed:
                continue
            
            async with aiofiles.open(file_path, "r") as f:
                content = await f.read()
                data = json.loads(content)
            
            deliveries.append(
                Delivery(
                    body=data,
                    meta={"file": str(file_path)}
                )
            )
            self.processed.add(file_path.name)
        
        return deliveries
    
    async def ack(self, delivery: Delivery):
        # 处理成功后删除文件
        file_path = delivery.meta.get("file")
        if file_path:
            Path(file_path).unlink()


class FileSink(Sink):
    """将消息写入文件"""
    
    def __init__(self, directory: str, prefix: str = "output"):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.prefix = prefix
        self.counter = 0
    
    async def publish(self, body, meta=None):
        self.counter += 1
        file_path = self.directory / f"{self.prefix}_{self.counter}.json"
        
        async with aiofiles.open(file_path, "w") as f:
            await f.write(json.dumps(body, indent=2))
```

## 使用自定义 Broker

```python
from onestep import OneStepApp

app = OneStepApp("custom-demo")

# 使用自定义 Source 和 Sink
source = RedisStreamSource("tasks", "group1", "consumer1")
sink = KafkaSink("results", "localhost:9092")


@app.task(source=source, emit=sink, concurrency=4)
async def process(ctx, item):
    return {"result": item}


if __name__ == "__main__":
    app.run()
```

## 注意事项

1. **连接管理**: 在 `connect()` 或 `fetch()` 中建立连接，在 `close()` 中释放
2. **错误处理**: 捕获并记录异常，避免影响主流程
3. **资源清理**: 确保 `close()` 被调用，避免资源泄漏
4. **并发安全**: 考虑多消费者场景的并发问题