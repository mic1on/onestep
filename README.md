<div align=center><img src="https://onestep.code05.com/logo-3.svg" width="300"></div>
<div align=center>
<a href="https://github.com/mic1on/onestep/actions/workflows/test.yml?query=event%3Apush+branch%3Amain" target="_blank">
    <img src="https://github.com/mic1on/onestep/workflows/test%20suite/badge.svg?branch=main&event=push" alt="Test">
</a>
<a href="https://pypi.org/project/onestep" target="_blank">
    <img src="https://img.shields.io/pypi/v/onestep.svg" alt="Package version">
</a>

<a href="https://pypi.org/project/onestep" target="_blank">
    <img src="https://img.shields.io/pypi/pyversions/onestep.svg" alt="Supported Python versions">
</a>

</div>
<hr />
仅需一步，轻松实现分布式异步任务。

## Brokers

- [x] MemoryBroker
- [x] CronBroker
- [x] WebHookBroker
- [x] RabbitMQBroker
- [x] RedisBroker
    - [x] RedisStreamBroker
    - [x] RedisPubSubBroker 
- [x] SQSBroker
- [x] MysqlBroker

## 😋example

### 基础用法

```python
# example.py

from onestep import step, WebHookBroker


# 对外提供一个webhook接口，接收外部的消息
@step(from_broker=WebHookBroker(path="/push"))
def waiting_messages(message):
    print("收到消息：", message)


if __name__ == '__main__':
    step.start(block=True)
```

also, you can use `onestep` command to start, like this:

```bash
$ onestep example
```

then, you can send a message to webhook:

```bash
$ curl -X POST -H "Content-Type: application/json" -d '{"a": 1}' http://localhost:8090/push
```

## 🤩 other brokers

```python
from onestep import step, CronBroker


# 每3秒触发一次任务
@step(from_broker=CronBroker("* * * * * */3", body={"a": 1}))
def cron_task(message):
    assert message.body == {"a": 1}
    return message


if __name__ == '__main__':
    step.start(block=True)
```

🤔more examples: [examples](example)

## ⚙️ Configuration

OneStep 支持通过配置文件和环境变量来管理配置，方便不同环境的部署。

### 配置文件

支持 `.json`、`.yaml`、`.yml` 格式的配置文件。

示例配置文件 `.onestep.yml`:

```yaml
# WebHook Broker 配置
webhook:
  host: "127.0.0.1"
  port: 8090
  api_key: "your-secret-key"

# Worker 配置
workers:
  default: 1
  max: 20
```

### 使用配置

```python
from onestep import step, WebHookBroker, Config

# 加载配置
config = Config(config_file=".onestep.yml")

# 使用配置
@step(
    from_broker=WebHookBroker.from_config(config),
    workers=config.get("workers.default", 1),
)
def handle_webhook(message):
    print(f"收到消息: {message}")

if __name__ == '__main__':
    step.start(block=True)
```

### 环境变量覆盖

支持通过环境变量覆盖配置值，格式为 `ONESTEP_<CONFIG_KEY>`。

嵌套键使用下划线分隔（例如 `webhook.port` → `ONESTEP_WEBHOOK_PORT`）。

```bash
# 覆盖配置
export ONESTEP_WEBHOOK_PORT=9000
export ONESTEP_WORKERS=5

# 运行应用
python app.py
```

类型会自动转换：数字、布尔值、null。

### 更多示例

查看完整示例：[`.onestep.yml.example`](.onestep.yml.example) 和 [`config_example.py`](example/config_example.py)

---

## 📚 Advanced Examples

### 多 Broker 组合

同时从多个 Broker 消费消息：

```python
from onestep import step, WebHookBroker, CronBroker, MemoryBroker

@step(
    from_broker=[
        WebHookBroker.from_config(config),
        CronBroker("* * * * * */5"),  # 每 5 秒触发一次
        MemoryBroker(),
    ],
    workers=3,
)
def multi_broker_handler(message):
    print(f"从多个 broker 收到消息: {message}")

if __name__ == '__main__':
    step.start(block=True)
```

### 中间件使用

使用中间件添加自定义逻辑：

```python
from onestep import step, WebHookBroker, UniqueMiddleware
from onestep.middleware import BaseMiddleware

class LoggingMiddleware(BaseMiddleware):
    def before_consume(self, message, step):
        print(f"[消费前] {message.body}")

    def after_consume(self, message, step):
        print(f"[消费后] {message.body}")

@step(
    from_broker=WebHookBroker.from_config(config),
    middlewares=[
        UniqueMiddleware(),
        LoggingMiddleware(),
    ],
)
def middleware_example(message):
    print(f"处理消息: {message}")

if __name__ == '__main__':
    step.start(block=True)
```

### 错误处理

使用重试策略和错误回调：

```python
from onestep import step, WebHookBroker, TimesRetry, Config

def error_callback(message):
    print(f"消息重试失败: {message}")

@step(
    from_broker=WebHookBroker.from_config(config),
    retry=TimesRetry(times=3),  # 最多重试 3 次
    error_callback=error_callback,
)
def error_handling_example(message):
    # 如果抛出异常，会自动重试
    if not message.get("valid"):
        raise ValueError("无效的消息")
    print(f"处理消息: {message}")

if __name__ == '__main__':
    step.start(block=True)
```

### 异步任务

处理异步任务：

```python
import asyncio

from onestep import step, WebHookBroker, Config

async def async_handler(message):
    await asyncio.sleep(1)
    print(f"异步处理: {message}")

@step(
    from_broker=WebHookBroker.from_config(config),
)
@step()
async def async_task(message):
    print(f"收到异步任务: {message}")

if __name__ == '__main__':
    step.start(block=True)
```

### Worker 线程池

使用线程池并发处理消息：

```python
from onestep import step, WebHookBroker, ThreadPoolWorker, Config

@step(
    from_broker=WebHookBroker.from_config(config),
    worker_class=ThreadPoolWorker,  # 使用线程池
    workers=10,  # 线程池大小
)
def threadpool_example(message):
    # 可以并发执行，提高吞吐量
    print(f"线程池处理: {message}")

if __name__ == '__main__':
    step.start(block=True)
```

### 自定义 Broker

继承 BaseBroker 实现自定义 Broker：

```python
from onestep.broker import BaseBroker, BaseConsumer
from queue import Queue

class CustomBroker(BaseBroker):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.queue = Queue()

    def publish(self, message, *args, **kwargs):
        # 实现发送逻辑
        self.queue.put(message)

    def consume(self, *args, **kwargs):
        # 返回消费者
        return self.queue

    def confirm(self, message):
        pass

    def reject(self, message):
        pass

    def requeue(self, message, is_source=False):
        if is_source:
            self.queue.put(message.message)
        else:
            self.queue.put(message)

@step(
    from_broker=CustomBroker(),
)
def custom_broker_example(message):
    print(f"自定义 broker: {message}")

if __name__ == '__main__':
    step.start(block=True)
```

### 消息转换和路由

使用 to_broker 将消息路由到不同的 Broker：

```python
from onestep import step, WebHookBroker, MemoryBroker, Config

# 接收 broker
input_broker = WebHookBroker.from_config(config)

# 路由 broker
success_broker = MemoryBroker()
error_broker = MemoryBroker()

@step(
    from_broker=input_broker,
    to_broker=success_broker,  # 成功的消息发送到这里
)
def process_success(message):
    print(f"处理成功: {message}")
    return {"status": "success", "data": message.body}

if __name__ == '__main__':
    step.start(block=True)
```

---

## 🔔 Signals

OneStep 使用 `blinker` 库提供信号系统，允许你监听和响应各种事件。

### 可用信号

| 信号名称 | 描述 | 参数 |
|---------|------|------|
| `started` | Step 启动时触发 | 无 |
| `stopped` | Step 停止时触发 | 无 |
| `message_received` | 收到消息时触发 | message, worker |
| `message_sent` | 消息发送成功时触发 | message, worker |
| `message_consumed` | 消息处理成功时触发 | message, worker |
| `message_error` | 消息处理出错时触发 | message, error, worker |
| `message_drop` | 消息被丢弃时触发 | message, reason, worker |
| `message_requeue` | 消息重新入队时触发 | message, reason, worker |

### 监听信号

```python
from onestep import step, WebHookBroker, Config
from onestep.signal import started, stopped, message_consumed, message_error

# 监听启动事件
@started.connect
def on_started():
    print("Step 已启动！")

# 监听停止事件
@stopped.connect
def on_stopped():
    print("Step 已停止！")

# 监听消息处理成功
@message_consumed.connect
def on_message_consumed(sender, message, **kwargs):
    print(f"消息处理成功: {message.body}")

# 监听消息处理错误
@message_error.connect
def on_message_error(sender, message, error, **kwargs):
    print(f"消息处理失败: {error}")

@step(
    from_broker=WebHookBroker.from_config(config),
)
def signal_example(message):
    print(f"处理消息: {message}")

if __name__ == '__main__':
    step.start(block=True)
```

### 监听特定 Step 的信号

```python
from onestep import step, WebHookBroker, Config
from onestep.signal import message_received

@step(from_broker=WebHookBroker.from_config(config))
def specific_step_handler(message):
    print(f"特定 step 处理: {message}")

# 只监听特定 step 的消息
@message_received.connect
def on_message_received(sender, message, **kwargs):
    # sender 是 step 实例
    if hasattr(sender, 'name') and sender.name == "specific_step_handler":
        print(f"特定 step 收到消息: {message}")

if __name__ == '__main__':
    step.start(block=True)
```

### 信号链

多个监听器会按注册顺序执行：

```python
from onestep.signal import message_received

@message_received.connect
def first_handler(sender, message, **kwargs):
    print(f"第一个处理器: {message.body}")

@message_received.connect
def second_handler(sender, message, **kwargs):
    print(f"第二个处理器: {message.body}")
```

---

## 🔔 More Examples

🤔更多示例: [examples](example)
