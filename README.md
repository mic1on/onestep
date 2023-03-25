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
from onestep import step, WebHookBroker


# 对外提供一个webhook接口，接收外部的消息
@step(from_broker=WebHookBroker(path="/push"))
def waiting_messages(message):
    print("收到消息：", message)


if __name__ == '__main__':
    step.start(block=True)
```

更多例子请参阅：[examples](example)