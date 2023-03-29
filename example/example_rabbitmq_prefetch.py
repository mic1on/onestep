import time

from onestep import step
from onestep.broker import RabbitMQBroker

step.set_debugging()

# 配置RabbitMQ队列及连接信息
rmq_broker = RabbitMQBroker(
    "test2",
    {
        "username": "admin",
        "password": "admin",
    }
)


# for i in range(5):
#     rmq_broker.send(str(i))


@step(from_broker=rmq_broker, workers=3)
def do_some_thing(message):
    print("do_some_thing", message.body)
    time.sleep(3)


if __name__ == '__main__':
    step.start(block=True)
