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
    from_broker=WebHookBroker(
        path="/webhook",
        host=config.get("webhook.host", "127.0.0.1"),
        port=config.get("webhook.port", 8090),
        api_key=config.get("webhook.api_key"),
    ),
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
