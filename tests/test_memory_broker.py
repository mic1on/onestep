from onestep.broker import MemoryBroker


def test_memory_broker_send():
    broker = MemoryBroker()
    message = "test message"
    broker.send(message)
    assert broker.queue.get() == message


def test_memory_broker_consume():
    broker = MemoryBroker()
    message = "test message"
    broker.send(message)
    consumer = broker.consume()
    assert next(consumer) == message
