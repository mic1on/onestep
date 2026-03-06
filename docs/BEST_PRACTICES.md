# OneStep 最佳实践

本文档提供使用 OneStep 的最佳实践和常见问题解决方案。

---

## 目录

- [生产环境部署](#生产环境部署)
- [性能优化](#性能优化)
- [错误处理](#错误处理)
- [监控和日志](#监控和日志)
- [测试策略](#测试策略)
- [常见问题](#常见问题)

---

## 生产环境部署

### 1. 使用持久化 Broker

**问题**：MemoryBroker 重启后丢失消息

**解决方案**：使用持久化 Broker

```python
# ❌ 不推荐：开发环境
from onestep import MemoryBroker
broker = MemoryBroker()

# ✅ 推荐：生产环境
from onestep import RabbitMQBroker
broker = RabbitMQBroker(
    host="rabbitmq.example.com",
    port=5672,
    username="onestep",
    password="xxx",
    queue="production_queue"
)

# 或者 Redis
from onestep import RedisStreamBroker
broker = RedisStreamBroker(
    host="redis.example.com",
    port=6379,
    queue="production_queue"
)
```

### 2. 配置管理

**推荐**：使用配置文件而不是硬编码

```python
# config.yml
brokers:
  input:
    type: rabbitmq
    host: rabbitmq.example.com
    port: 5672
    queue: input_queue
  output:
    type: redis
    host: redis.example.com
    port: 6379
    queue: output_queue

workers:
  min: 2
  max: 10

retry:
  max_attempts: 5
  backoff: exponential
```

```python
# app.py
from onestep import step, Config
from onestep.broker import RabbitMQBroker, RedisStreamBroker

config = Config.from_yaml("config.yml")

@step(from_broker=RabbitMQBroker.from_config(config, key="brokers.input"),
       to_broker=RedisStreamBroker.from_config(config, key="brokers.output"),
       workers=config.get("workers.min", 2))
def process(message):
    return transform(message)
```

### 3. Worker 数量配置

**建议**：根据 CPU 核心数和任务类型调整

```python
# CPU 密集型任务
import os
@step(workers=os.cpu_count())
def cpu_intensive_task(message):
    return heavy_computation(message)

# IO 密集型任务
@step(workers=os.cpu_count() * 2)
def io_intensive_task(message):
    return network_call(message)

# 默认值（安全）
@step(workers=4)
def normal_task(message):
    return process(message)
```

### 4. 日志脱敏

**问题**：日志可能包含敏感信息

**解决方案**：启用自动脱敏

```python
# 环境变量
export ONESTEP_LOG_REDACT=true

# 或者代码配置
from onestep import step
from onestep.broker import RabbitMQBroker

@step(from_broker=RabbitMQBroker(...))
def process(message):
    # 自动脱敏：password, token, api_key, email 等
    logger.info(f"Processing: {message}")  # 脱敏后：Processing: {body: '***'}
    return result
```

---

## 性能优化

### 1. 使用连接池

**问题**：每次创建连接开销大

**解决方案**：使用连接池

```python
from onestep import step
from onestep.broker import RabbitMQBroker

# ✅ 推荐：连接池
broker = RabbitMQBroker(
    host="rabbitmq.example.com",
    connection_pool_size=10,  # 连接池大小
    channel_pool_size=20       # channel 池大小
)

@step(from_broker=broker)
def process(message):
    return process_with_db(message)
```

### 2. 批处理优化

**问题**：逐条处理效率低

**解决方案**：使用批处理

```python
from onestep import step
from onestep.broker import RedisStreamBroker

@step(from_broker=RedisStreamBroker(...))
def process(message):
    # 批量写入
    batch_size = 100
    if len(current_batch) >= batch_size:
        db.bulk_insert(current_batch)
        current_batch.clear()
    return result
```

### 3. 中间件优化

**建议**：只启用必要的中间件

```python
from onestep import step
from onestep.middleware import LoggingMiddleware, UniqueMiddleware

# ✅ 推荐：精简中间件链
@step(
    from_broker=broker,
    middlewares=[
        UniqueMiddleware(),  # 去重
        LoggingMiddleware(order=10),  # 日志（优先执行）
    ]
)
def process(message):
    return result

# ❌ 不推荐：过多中间件
@step(
    from_broker=broker,
    middlewares=[
        LoggingMiddleware(),
        UniqueMiddleware(),
        CacheMiddleware(),
        RateLimitMiddleware(),
        AuthMiddleware(),
        ...  # 过多中间件影响性能
    ]
)
def process(message):
    return result
```

### 4. 异步处理

**建议**：IO 密集型任务使用异步

```python
import asyncio

# ✅ 推荐：异步
@step(from_broker=broker)
async def process_async(message):
    result = await asyncio.sleep(1)
    return result

# ❌ 不推荐：同步（IO 密集型）
@step(from_broker=broker)
def process_sync(message):
    time.sleep(1)  # 阻塞
    return result
```

---

## 错误处理

### 1. 重试策略

**推荐**：根据任务类型选择重试策略

```python
from onestep import step
from onestep.retry import TimesRetry, RetryIfException, AdvancedRetry

# 固定次数重试
@step(retry=TimesRetry(times=3))
def task_with_fixed_retry(message):
    return may_fail(message)

# 基于异常类型重试
@step(retry=RetryIfException(
    exceptions=(TimeoutError, ConnectionError),
    max_attempts=5
))
def task_with_exception_retry(message):
    return may_timeout(message)

# 高级重试（指数退避 + 抖动）
@step(retry=AdvancedRetry(
    max_attempts=5,
    initial_delay=1.0,
    max_delay=60.0,
    jitter=True  # 避免雷群效应
))
def task_with_advanced_retry(message):
    return may_fail_unpredictably(message)
```

### 2. 错误回调

**推荐**：为关键任务添加错误回调

```python
from onestep import step

def error_handler(message):
    # 发送告警
    send_alert(
        title="Task Failed",
        body=f"Message {message.id} failed: {message.exception}"
    )
    # 记录到死信队列
    dead_letter_queue.put(message)

@step(error_callback=error_handler)
def critical_task(message):
    return must_succeed(message)
```

### 3. 幂等性保证

**问题**：重试可能导致重复处理

**解决方案**：使用幂等中间件

```python
from onestep import step
from onestep.middleware import MemoryUniqueMiddleware

@step(
    from_broker=broker,
    middlewares=[MemoryUniqueMiddleware()]
)
def process(message):
    # 即使重试，也不会重复处理
    return insert_to_db(message.id, message.data)
```

---

## 监控和日志

### 1. 日志级别

**建议**：生产环境使用 INFO 或 WARNING

```python
import logging

# 生产环境
logging.getLogger("onestep").setLevel(logging.INFO)

# 开发环境
logging.getLogger("onestep").setLevel(logging.DEBUG)
```

### 2. 结构化日志

**推荐**：使用结构化日志格式

```python
import json
import logging

class JsonFormatter(logging.Formatter):
    def format(self, record):
        log_data = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
        }
        return json.dumps(log_data)

logger = logging.getLogger("onestep")
handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
logger.addHandler(handler)
```

### 3. 监控指标

**建议**：收集关键指标

```python
from onestep import step
from onestep.signal import message_consumed, message_error
from prometheus_client import Counter, Histogram

# 指标
message_counter = Counter('onestep_messages_total', 'Total messages')
message_latency = Histogram('onestep_message_latency_seconds', 'Message latency')
error_counter = Counter('onestep_errors_total', 'Total errors', ['error_type'])

# 监控消息消费
message_consumed.connect(
    lambda sender, message, **kwargs: message_counter.inc()
)

# 监控消息错误
message_error.connect(
    lambda sender, message, error, **kwargs: error_counter.labels(
        error_type=type(error).__name__
    ).inc()
)

@step(from_broker=broker)
def process(message):
    with message_latency.time():
        return process_message(message)
```

---

## 测试策略

### 1. 使用 Fake Broker

**推荐**：单元测试使用 Fake Broker

```python
import pytest
from onestep import step, MemoryBroker

# ✅ 推荐：使用 MemoryBroker（轻量级）
@pytest.mark.unit
def test_processing_logic():
    broker = MemoryBroker()

    @step(from_broker=broker)
    def process(message):
        return message.body * 2

    result = process.fn(5)
    assert result == 10

# ❌ 不推荐：使用真实 Broker
@pytest.mark.integration
def test_with_real_rabbitmq():
    broker = RabbitMQBroker(...)  # 慢、依赖外部服务
    ...
```

### 2. Mock 外部依赖

**推荐**：Mock 数据库、API 等外部依赖

```python
from unittest.mock import Mock, patch
from onestep import step, MemoryBroker

@pytest.mark.unit
def test_with_mock_db():
    broker = MemoryBroker()
    mock_db = Mock()
    mock_db.insert.return_value = True

    @step(from_broker=broker)
    def process(message):
        return mock_db.insert(message.body)

    result = process.fn({"data": "value"})
    assert mock_db.insert.called
    assert result
```

### 3. 测试中间件

**推荐**：单独测试中间件逻辑

```python
from onestep.middleware import BaseMiddleware
from onestep.message import Message

class TestMiddleware(BaseMiddleware):
    def __init__(self, order=50):
        super().__init__(order=order)

    def before_consume(self, step, message, *args, **kwargs):
        message.extra['test'] = True

@pytest.mark.unit
def test_middleware():
    middleware = TestMiddleware()
    message = Message(body="test")

    middleware.before_consume(None, message)
    assert message.extra.get('test') is True
```

---

## 常见问题

### Q1: 如何避免消息重复消费？

**A**: 使用去重中间件

```python
from onestep import step
from onestep.middleware import RedisUniqueMiddleware

@step(
    from_broker=broker,
    middlewares=[RedisUniqueMiddleware()]
)
def process(message):
    return result
```

### Q2: 如何处理消息处理失败？

**A**: 使用重试策略和错误回调

```python
from onestep import step
from onestep.retry import TimesRetry

def error_handler(message):
    # 记录失败消息
    logger.error(f"Failed: {message}")

@step(
    retry=TimesRetry(times=3),
    error_callback=error_handler
)
def process(message):
    return may_fail(message)
```

### Q3: 如何实现消息优先级？

**A**: 使用 Broker 的优先级队列功能

```python
from onestep import step
from onestep.broker import RabbitMQBroker

broker = RabbitMQBroker(
    ...,
    queue_args={
        "x-max-priority": 10  # 0-10，数值越高优先级越高
    }
)

@step(from_broker=broker)
def process(message):
    return result
```

### Q4: 如何实现延迟消息？

**A**: 使用 Broker 的延迟队列功能

```python
from onestep import step
from onestep.broker import RabbitMQBroker

broker = RabbitMQBroker(
    ...,
    queue_args={
        "x-delayed-type": "direct"  # 延迟队列
    }
)

@step(from_broker=broker)
def process(message):
    return result
```

### Q5: 如何实现消息超时？

**A**: 使用中间件或 Broker 的 TTL 功能

```python
from onestep.middleware import BaseMiddleware

class TimeoutMiddleware(BaseMiddleware):
    def __init__(self, timeout_seconds=30):
        super().__init__()
        self.timeout = timeout_seconds

    def before_consume(self, step, message, *args, **kwargs):
        message.extra['start_time'] = time.time()

    def after_consume(self, step, message, *args, **kwargs):
        start_time = message.extra.get('start_time')
        if start_time and time.time() - start_time > self.timeout:
            logger.warning(f"Message timeout: {message.id}")

@step(
    from_broker=broker,
    middlewares=[TimeoutMiddleware(timeout_seconds=30)]
)
def process(message):
    return result
```

### Q6: 如何实现消息确认机制？

**A**: 使用 Broker 的 confirm 机制

```python
from onestep import step
from onestep.broker import RabbitMQBroker

# Publisher Confirms
broker = RabbitMQBroker(...)

# 手动确认
@step(from_broker=broker)
def process(message):
    try:
        result = process_message(message)
        message.confirm()  # 手动确认
        return result
    except Exception as e:
        message.reject()  # 拒绝消息
        raise
```

### Q7: 如何实现死信队列？

**A**: 配置 Broker 的死信队列

```python
from onestep import step
from onestep.broker import RabbitMQBroker

broker = RabbitMQBroker(
    ...,
    queue_args={
        "x-dead-letter-exchange": "dlx",  # 死信交换机
        "x-dead-letter-routing-key": "dlq"  # 死信队列
    }
)

@step(from_broker=broker)
def process(message):
    return result
```

### Q8: 如何实现消息过滤？

**A**: 使用中间件或 Broker 的路由功能

```python
from onestep.middleware import BaseMiddleware
from onestep.exception import DropMessage

class FilterMiddleware(BaseMiddleware):
    def __init__(self, filter_func):
        super().__init__()
        self.filter = filter_func

    def before_consume(self, step, message, *args, **kwargs):
        if not self.filter(message.body):
            raise DropMessage(f"Message filtered: {message}")

@step(
    from_broker=broker,
    middlewares=[FilterMiddleware(lambda m: m.get('valid', False))]
)
def process(message):
    return result
```

### Q9: 如何实现消息分片？

**A**: 使用多队列和动态路由

```python
from onestep import step
from onestep.broker import RabbitMQBroker

# 创建多个队列
queues = [
    RabbitMQBroker(..., queue="queue_1"),
    RabbitMQBroker(..., queue="queue_2"),
    RabbitMQBroker(..., queue="queue_3"),
]

# 基于消息 ID 分片
def get_queue(message_id):
    return queues[hash(message_id) % len(queues)]

@step(from_broker=get_queue(message_id))
def process(message):
    return result
```

### Q10: 如何实现消息聚合？

**A**: 使用批处理中间件

```python
from onestep.middleware import BaseMiddleware
import time
from collections import defaultdict

class BatchMiddleware(BaseMiddleware):
    def __init__(self, batch_size=100, timeout=5.0):
        super().__init__()
        self.batch_size = batch_size
        self.timeout = timeout
        self.batches = defaultdict(list)
        self.last_flush = time.time()

    def before_consume(self, step, message, *args, **kwargs):
        batch_key = step.name
        self.batches[batch_key].append(message)

        if (len(self.batches[batch_key]) >= self.batch_size or
            time.time() - self.last_flush > self.timeout):
            # 刷新批次
            batch = self.batches.pop(batch_key)
            process_batch(batch)
            self.last_flush = time.time()

@step(
    from_broker=broker,
    middlewares=[BatchMiddleware(batch_size=100, timeout=5.0)]
)
def process(message):
    return result
```

---

## 更多资源

- [GitHub Issues](https://github.com/mic1on/onestep/issues)
- [API 文档](https://docs.onestep.ai)
- [社区论坛](https://community.onestep.ai)
