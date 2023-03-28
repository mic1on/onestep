from onestep import step
from onestep.broker import RabbitMQBroker, MemoryBroker

step.set_debugging()

# 配置RabbitMQ队列及连接信息
rmq_broker = RabbitMQBroker(
    "test",
    {
        "username": "admin",
        "password": "admin",
    }
)

# 模拟一个内存队列
todo_broker = MemoryBroker()
todo_broker.send("1")


@step(from_broker=todo_broker, to_broker=rmq_broker)
def build_todo_list(message):
    print("build_todo_list", message.body)
    # 返回的内容将发给RabbitMQ队列
    return "build_todo_list"


if __name__ == '__main__':
    step.start(block=True)
