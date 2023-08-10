---
outline: deep
title: Broker
---

## Broker

### 初识Broker

Broker是一个消息代理，它为多任务之间的通信提供了一个中心点。

在一段最简单的`@step`中，来阐述一下Broker的作用：

```python
from onestep import step, CronBroker

build_job_broker = CronBroker("* * * * * */3")


@step(from_broker=build_job_broker)
def task_one(message):
    # to do some work
    print(message)


step.start(block=True)
```

- 使用[CronBroker](/broker/cron)来定义一个定时任务
- 使用`@step`装饰器来定义一个任务
- 使用`from_broker`参数来指定任务触发来源

在`start`启动后方可在控制台每隔三秒钟输出：
```
{'body': {}, 'extra': {'task_id': 'f2fa539b-c7f3-4e2a-a938-3c62582baf8a', 'publish_time': 1691682525.791, 'failure_count': 0}}
{'body': {}, 'extra': {'task_id': 'e9ed0e2a-7776-4acd-9179-168be04f696f', 'publish_time': 1691682528.799, 'failure_count': 0}}
{'body': {}, 'extra': {'task_id': '9b70ca99-1c5a-4d61-a038-57960983e0b1', 'publish_time': 1691682531.808, 'failure_count': 0}}
```

### 数据流转

解决了数据从哪里来的问题，接下来要解决数据流向哪里。`to_broker`参数就是来定义任务最终完成的结果发给谁。

依然是上面的代码
```python{4-7}
from onestep import step, CronBroker, RabbitMQBroker

build_job_broker = CronBroker("* * * * * */3")
list_job_broker = RabbitMQBroker("list_job", {
    "username": "admin",
    "password": "admin",
})


@step(from_broker=build_job_broker, to_broker=list_job_broker)
def task_one(message):
    return 


step.start(block=True)
```
