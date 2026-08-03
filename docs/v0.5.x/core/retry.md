---
outline: deep
title: Retry
---

## Retry

重试组件可以在任务执行失败时达到某种条件时，自动重试任务。


## 内置的重试组件

### NeverRetry

永不重试，任务失败后不会重试。

### AlwaysRetry

无条件重试，任务失败后会无限重试。

### TimesRetry

重试次数，任务失败后会重试指定次数。

```python
from onestep import step, TimesRetry

# 重试3次任务
@step(from_broker=..., retry=TimesRetry(3))
def add(message):
    print(message.body)
    raise Exception('error')
```

### RetryIfException

重试条件，任务失败后会重试，但是只有当抛出指定的异常时才会重试。

```python
from onestep import step, RetryIfException

# 重试3次任务
@step(from_broker=..., retry=RetryIfException(Exception))
def add(message):
    print(message.body)
    raise Exception('error')
```

### AdvancedRetry

高级重试策略

1. 本地重试：如果异常是 RetryViaLocal 或 指定异常，且重试次数未达到上限，则本地重试，不回调
2. 队列重试：如果异常是 RetryViaQueue，且重试次数未达到上限，则入队重试，不回调
3. 其他异常：如果异常不是 RetryViaLocal 或 RetryViaQueue 或 指定异常，则不重试，回调
注：待重试的异常若继承自 RetryException，则可单独指定重试次数，否则默认为 3 次

详见[Example](https://github.com/mic1on/onestep/blob/main/example/example_advanced_retry.py)


### 自定义重试组件

::: warning ⚠️继承关系
自定义重试组件必须继承自`BaseRetry`
:::
