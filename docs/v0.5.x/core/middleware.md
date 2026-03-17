---
outline: deep
title: Middleware
---

## Middleware

中间件在`@step`中是一个可选的组件，它可以在任务执行前后做一些事情。

其中内置四个方法来hook任务执行中的行为：

- `before_send`：消息发送之前
- `after_send`：消息发送之后
- `before_consume`：消费消息之前
- `after_consume`：消费消息之后

```python
class BaseMiddleware:

    def before_send(self, step, message, *args, **kwargs):
        """消息发送之前"""

    def after_send(self, step, message, *args, **kwargs):
        """消息发送之后"""

    def before_consume(self, step, message, *args, **kwargs):
        """消费消息之前"""

    def after_consume(self, step, message, *args, **kwargs):
        """消费消息之后"""
```


## 跳过后续中间件

当你想在中间件中判断某些条件，如果满足条件则跳过后续中间件，可以直接抛出`StopMiddleware`异常。

```python
from onestep import BaseMiddleware, StopMiddleware

class MyMiddleware(BaseMiddleware):

    def before_consume(self, step, message, *args, **kwargs):
        raise StopMiddleware()
```


## 丢弃消息

当你想在中间件中判断某些条件，如果满足条件则丢弃消息，可以直接抛出`DropMessage`异常。

内置`UniqueMiddleware`就是这样的一个[中间件](https://github.com/mic1on/onestep/blob/main/src/onestep/middleware/unique.py)，它会判断消息是否已经被消费过，如果已经被消费过则丢弃消息。


```python{7}
class UniqueMiddleware(BaseMiddleware):
    default_hash_func = hash_func()

    def before_consume(self, step, message, *args, **kwargs):
        message_hash = self.default_hash_func(message.body)
        if self.has_seen(message_hash):
            raise DropMessage(f"Message<{message}> has been seen before")
```


## 自定义中间件

::: warning ⚠️继承关系
自定义中间件必须继承自`BaseMiddleware`
:::

你可以在任务的流转过程中，自定义中间件来做一些事情。

```python
from onestep import BaseMiddleware

class MyMiddleware(BaseMiddleware):

    def before_consume(self, step, message, *args, **kwargs):
        print("before consume")

    def after_consume(self, step, message, *args, **kwargs):
        print("after consume")
```

然后在`@step`中使用`middlewares`参数来指定中间件。

```python
from onestep import step

@step(from_broker=..., middlewares=[MyMiddleware()])
def my_task(message):
    print("my task")
```
