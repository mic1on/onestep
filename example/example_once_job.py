import threading

from onestep import MemoryBroker, step, BaseMiddleware


class MyMiddleware(BaseMiddleware):

    def before_send(self, step, message, *args, **kwargs):
        print("called before send")

    def after_send(self, step, message, *args, **kwargs):
        print("called after send")

    def before_consume(self, step, message, *args, **kwargs):
        print("called before receive")

    def after_consume(self, step, message, *args, **kwargs):
        print("called after receive")


memory_broker = MemoryBroker(once=True)
memory_broker2 = MemoryBroker()


@step(from_broker=memory_broker,
      to_broker=memory_broker2,
      middlewares=[MyMiddleware()])
def once_job(message):
    print(message)
    return message


threading.Thread(target=step.start, kwargs={'block': True}).start()

memory_broker.publish("the first message")
