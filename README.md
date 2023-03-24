# OneStep

内部测试阶段，请勿用于生产

## example

```python
from onestep import step
from onestep.broker import WebHookBroker

step.set_debugging()

webhook_broker = WebHookBroker(path="/push")


@step(from_broker=webhook_broker)
def build_todo_list(message):
    return 1


if __name__ == '__main__':
    step.start(block=True)
```