from onestep import step
from onestep.broker import RabbitMQBroker, MemoryBroker

step.set_debugging()
rmq_broker = RabbitMQBroker(
    "test",
    {
        "username": "admin",
        "password": "admin",
    }
)
todo_broker = MemoryBroker()
todo_broker.send("1")


@step(from_broker=todo_broker, to_broker=rmq_broker)
def build_todo_list(message):
    print("build_todo_list", message.message)
    return "build_todo_list"


if __name__ == '__main__':
    step.start(block=True)
