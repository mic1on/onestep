---
outline: deep
title: MemoryBroker
---

## MemoryBroker

`MemoryBroker`是一个基于`Queue`的内存消息队列，用于在同一个进程内部的消息传递。

```python{8}
from onestep import step
from onestep.broker import MemoryBroker

broker = MemoryBroker()
broker.queue.put("test")  # 模拟手动发一条消息


@step(from_broker=broker)
def step1(message):
    print(message.body)


step.start(block=True)
```
