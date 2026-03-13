from onestep import step
from onestep.broker import MemoryBroker
from onestep.exception import RetryInQueue, DropMessage
from onestep.retry import AdvancedRetry

todo_broker = MemoryBroker()


@step(to_broker=todo_broker)
def build_todo_list():
    # mock data
    yield from [
        {
            "id": 1,
            "title": "todo1",
            "content": "todo1 content",
            "status": "todo",
        },
        {
            "id": 2,
            "title": "todo2",
            "content": "todo2 content",
            "status": "todo",
        },
        {
            "id": 3,
            "title": "todo3",
            "content": "todo3 content",
            "status": "todo",
        },
    ]


@step(from_broker=todo_broker, workers=1,
      retry=AdvancedRetry()
      )
def do_something(todo):
    print(todo)
    todo.body["status"] = "done"
    if todo.body["id"] == 2:
        todo.body["id"] = 21
        raise RetryInQueue("test requeue")
    elif todo.body["id"] == 3:
        raise DropMessage("test reject")
    else:
        return todo  # test confirm


if __name__ == '__main__':
    import time
    step.set_debugging()

    build_todo_list()
    step.start(block=False)
    time.sleep(5)
    step.shutdown()
