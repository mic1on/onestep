from loguru import logger

from onestep import step
from onestep.broker import MemoryBroker
from onestep.retry import TimesRetry, RetryIfException, AllRetry

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
    yield from range(10)


def callback_on_failure(message):
    # 这里可以按需记录到日志或者发送到失败队列
    logger.warning(f"failure_callback: will send to failure queue: {message}")
    logger.debug(message.to_json(True))
    pass


@step(
    from_broker=todo_broker,
    retry=AllRetry(TimesRetry(), RetryIfException((ValueError,))),
    error_callback=callback_on_failure
)
def do_something(message):
    logger.info(f"todo: {message.body}")
    # return
    # if message.body.get("id") == 1:
    #     raise RetryViaLocal("Invalid id")
    if message.body == 1:
        raise ValueError("Invalid id")


if __name__ == "__main__":
    step.set_debugging()
    build_todo_list()
    step.start(block=True)
