---
outline: deep
title: CronBroker
---

## CronBroker

`CronBroker`是一个定时自执行的Broker，它允许你通过配置[Cron表达式](https://github.com/kiorky/croniter)来自动执行一些任务。

```python{4}
from onestep import step, CronBroker


@step(from_broker=CronBroker("* * * * * */3", name="miclon"))
def cron_task(message):
    print(message)
    return message


if __name__ == '__main__':
    step.start(block=True)
```

上述代码会每隔3秒执行一次`cron_task`，并且会将`name`的值传递给消息体。

```text
{'body': {'name': 'miclon'}, 'extra': {'task_id': '99c73c21-6473-4be5-8836-bfc0ddcdf8c3', 'publish_time': 1691546688.498, 'failure_count': 0}}
{'body': {'name': 'miclon'}, 'extra': {'task_id': 'ba79bd1a-fc60-46d8-8a44-999f715729f0', 'publish_time': 1691546691.505, 'failure_count': 0}}
```