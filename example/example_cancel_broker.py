import threading

from onestep import MemoryBroker, step


def cancel_consume(message):
    # when receive "over", cancel consume
    if message.body == "over":
        return True


memory_broker = MemoryBroker(cancel_consume=cancel_consume)


@step(from_broker=memory_broker)
def once_job(message):
    # do something
    print("receive", message.body)


if __name__ == '__main__':
    threading.Thread(target=step.start, kwargs={'block': True}).start()

    for i in range(2):
        memory_broker.publish(i)
    memory_broker.publish("over")
