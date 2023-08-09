---
outline: deep
title: WebHook
---

## WebHook

`WebHook`是一个基于`HTTP`的Broker，它可以将任务对外暴露一个HTTP接口，用于接收来自`HTTP`请求的消息。

```python{7}
from onestep import step, WebHookBroker

# 对外提供一个webhook接口，接收外部的消息
webhook_broker = WebHookBroker(path="/push")


@step(from_broker=webhook_broker)
def waiting_messages(message):
    print("收到消息：", message)


step.start(block=True)
```

上述代码会在`http://127.0.0.1:8090/push` 提供一个`POST`接口，用于接收消息。

::: info
默认端口为`8090` ，可以通过`port`参数来修改。
:::

```bash
curl -H "Content-Type: application/json" -X POST -d '{"body": "Hello OneStep"}' "http://127.0.0.1:8090/push"
```

```text
收到消息： {'body': 'Hello OneStep', 'extra': {'task_id': '2a922df2-2419-4978-9184-4227558d641b', 'publish_time': 1691568238.781, 'failure_count': 0}}
```
