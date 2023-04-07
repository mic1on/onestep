import logging

from onestep import step
from onestep.broker import MemoryBroker, RabbitMQBroker
from onestep.exception import RetryViaLocal, RetryViaQueue
from onestep.retry import AdvancedRetry

from logging import getLogger

logger = getLogger(__name__)
logger.setLevel(logging.INFO)

todo_broker = MemoryBroker()


# todo_broker = RabbitMQBroker(
#     "test",
#     {
#         "username": "admin",
#         "password": "admin",
#     }
# )

@step(to_broker=todo_broker)
def build_todo_list():
    # mock data
    yield from [
        {
            "id": 1,
            "title": "todo1",
            "content": "todo1 content",
            "status": "todo",
        },
        {
            "id": 2,
            "title": "todo2",
            "content": "todo2 content",
            "status": "todo",
        },
        {
            "id": 3,
            "title": "todo3",
            "content": "todo3 content",
            "status": "todo",
        },
        {
            "id": 4,
            "title": "todo4",
            "content": "todo4 content",
            "status": "todo",
        },
    ]


def callback_on_failure(message):
    # 这里可以按需记录到日志或者发送到失败队列
    logger.warning(f"failure_callback: will send to failure queue: {message}")
    pass


@step(
    from_broker=todo_broker,
    retry=AdvancedRetry(times=3, exceptions=(ValueError,)),
    error_callback=callback_on_failure
)
def do_something(message):
    print(f"todo: {message.body}")
    # return
    if message.body.get("id") == 1:
        raise RetryViaLocal("Invalid id")
    elif message.body.get("id") == 2:
        raise RetryViaQueue("Invalid id", times=2)
    elif message.body.get("id") == 3:
        raise ValueError("Invalid id")
    else:
        message.body["status"] = "done"
        return None


if __name__ == "__main__":
    step.set_debugging()
    build_todo_list()
    step.start(block=True)
