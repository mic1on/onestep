import logging

from onestep import step
from onestep.broker import MemoryBroker, RabbitMQBroker
from onestep.exception import RetryViaLocal, RetryViaQueue
from onestep.retry import MyRetry

from logging import getLogger

logger = getLogger(__name__)
logger.setLevel(logging.INFO)

todo_broker = MemoryBroker()

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
    ]


# def retry_via_queue_manual(message):
#     print("error_callback", message)
#     extra = message.body.get("extra", {})
#     extra['retry_times'] = extra.get("retry_times", 0) + 1
#     if extra['retry_times'] < 3:
#         message.body['extra'] = extra
#         todo_broker.send(message)


def callback_on_failure(message):
    logger.warning(f"failure_callback: will send to failure queue: {message}")
    pass


@step(from_broker=todo_broker,
      retry=MyRetry(times=3),
      error_callback=callback_on_failure)
def do_something(message):
    print(f"todo: {message.body}")
    if message.body.get("id") % 2 == 0:
        raise RetryViaLocal("Invalid id")
    if message.body.get("id") % 3 == 0:
        raise RetryViaQueue("Invalid id", times=2)
    else:
        message.body["status"] = "done"
        return message


if __name__ == "__main__":
    step.set_debugging()
    build_todo_list()
    step.start(block=True)
