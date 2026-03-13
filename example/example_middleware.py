from onestep import step, MemoryBroker, BaseMiddleware

todo_broker = MemoryBroker()
todo_broker.send("hello world")


class MyMiddleware(BaseMiddleware):
    def __init__(self):
        self.x = 123

    def before_send(self, step, message, *args, **kwargs):
        print(self.x)

    def after_send(self, step, message, *args, **kwargs):
        pass

    def before_consume(self, step, message, *args, **kwargs):
        pass

    def after_consume(self, step, message, *args, **kwargs):
        pass


# 由于from_broker和to_broker都是todo_broker，
# 所以这个step会在todo_broker中循环执行
@step(from_broker=todo_broker,
      to_broker=todo_broker,
      workers=3,
      middlewares=[MyMiddleware()])
def do_something(todo):
    return todo


if __name__ == '__main__':
    # step.set_debugging()

    step.start(block=True)
