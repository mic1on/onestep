from onestep.event import event, Event


def test_on_event():
    e = Event()
    result = []
    e.on("test", lambda: result.append(1))
    e.emit("test")
    assert result == [1]


def test_remove_event():
    e = Event()
    result = []

    def fn():
        result.append(1)

    e.on("test", fn)
    e.remove("test", fn)
    e.emit("test")
    assert result == []


def test_all_event():
    result = []

    def fn():
        result.append(1)

    event.on("test", fn)
    assert event.all() == {"test": [fn]}
