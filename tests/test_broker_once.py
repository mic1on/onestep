import pytest
from onestep.broker import BaseLocalBroker


@pytest.fixture
def broker():
    return BaseLocalBroker(once=True)


def test_once(broker):
    consumer = broker.consume()
    broker.send("message 1")
    broker.send("message 2")
    broker.send("message 3")

    # Consume only one message
    consumed_messages = []
    for message in consumer:
        consumed_messages.append(message)
        if broker.once:
            break

    assert len(consumed_messages) == 1
