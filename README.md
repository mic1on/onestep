# OneStep

<a href="https://github.com/mic1on/onestep/actions/workflows/test.yml?query=event%3Apush+branch%3Amain" target="_blank">
    <img src="https://github.com/mic1on/onestep/workflows/test%20suite/badge.svg?branch=main&event=push" alt="Test">
</a>
<a href="https://pypi.org/project/onestep" target="_blank">
    <img src="https://img.shields.io/pypi/v/onestep.svg" alt="Package version">
</a>

<a href="https://pypi.org/project/onestep" target="_blank">
    <img src="https://img.shields.io/pypi/pyversions/onestep.svg" alt="Supported Python versions">
</a>

<hr />
仅需一步，轻松实现分布式异步任务。

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