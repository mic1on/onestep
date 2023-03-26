from onestep import step, MemoryBroker, BaseMiddleware

todo_broker = MemoryBroker()
todo_broker.send("hello world")


class MyMiddleware(BaseMiddleware):
    def before_send(self, message):
        print(f"before send: {message}")

    def after_send(self, message):
        print(f"after send: {message}")

    def before_receive(self, message):
        print(f"before receive: {message}")

    def after_receive(self, message):
        print(f"after receive: {message}")


# 由于from_broker和to_broker都是todo_broker，
# 所以这个step会在todo_broker中循环执行
@step(from_broker=todo_broker,
      to_broker=todo_broker,
      workers=3,
      middlewares=[MyMiddleware])
def do_something(todo):
    return todo


if __name__ == '__main__':
    # step.set_debugging()

    step.start(block=True)
