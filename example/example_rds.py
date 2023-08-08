from onestep import step
from onestep.broker import RedisStreamBroker, MemoryBroker

step.set_debugging()

# 配置队列及连接信息
rds_broker = RedisStreamBroker(stream="ccd", group="demo-group", params={
    "password": "123456",
})

# 模拟一个内存队列
todo_broker = MemoryBroker()


# todo_broker.send("1")


@step(from_broker=rds_broker, to_broker=todo_broker)
def build_todo_list(message):
    print("build_todo_list", message.body)
    # 返回的内容将发给RabbitMQ队列
    return message


if __name__ == '__main__':
    step.start(block=True)
