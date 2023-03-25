from onestep import step
from onestep.broker import MemoryBroker

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


@step(from_broker=todo_broker,
      workers=3)
def do_something(todo):
    todo.message["status"] = "done"
    return todo


if __name__ == '__main__':
    step.set_debugging()

    build_todo_list()
    step.start(block=True)
