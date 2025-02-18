import time

from onestep import step
from onestep.broker import RabbitMQBroker, MemoryBroker
from loguru import logger

step.set_debugging()

# 配置RabbitMQ队列及连接信息，设置优先级为10
rmq_broker = RabbitMQBroker(
    "onstep_test_priority",
    {
        "username": "admin",
        "password": "admin",
    },
    queue_params={"arguments": {"x-max-priority": 10}},
)

# 模拟一个内存队列
todo_broker = MemoryBroker()


# 发送三个不同优先级的消息，其中一个默认优先级
# rmq_broker.send("1, priority=default")
# rmq_broker.send({"hello": "world", "priority": 5}, properties={"priority": 5})
# rmq_broker.send("3, priority=1", properties={"priority": 1})


@step(from_broker=rmq_broker, to_broker=todo_broker, workers=3)
def build_todo_list(message):
    logger.debug("build_todo_list", message.body)
    # 返回的内容将发给RabbitMQ队列
    return message


if __name__ == "__main__":
    step.start()
    time.sleep(5)
    step.shutdown()
    # rmq_broker.shutdown()
    pass
