# OneStep

内部测试阶段，请勿用于生产。

## Brokers

- [x] MemoryBroker
- [x] RabbitMQBroker
- [x] WebHookBroker
- [ ] RedisBroker
- [ ] KafkaBroker

## example

```python
from onestep import step
from onestep.broker import WebHookBroker

step.set_debugging()

# 对外提供一个webhook接口，接收外部的消息
webhook_broker = WebHookBroker(path="/push")


@step(from_broker=webhook_broker)
def waiting_messages(message):
    print("收到消息：", message)


if __name__ == '__main__':
    step.start(block=True)
```

更多例子请参阅：[examples](example)