from onestep.broker import MemoryBroker


def test_cancel_consume():
    def cancel_consume(message):
        if message.body == "cancel":
            return True
        return False

    broker = MemoryBroker(cancel_consume=cancel_consume)
    # Add messages to the queue
    broker.queue.put("message 1")
    broker.queue.put("cancel")
    broker.queue.put("message 2")

    consumed_messages = []
    for message in broker.consume():
        if broker.cancel_consume(message):
            break
        consumed_messages.append(message)

    assert len(consumed_messages) == 1
    assert consumed_messages[0].body == "message 1"
