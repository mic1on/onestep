import time
from random import randint

from onestep.message import Message

from queue import Queue
import pytest

from onestep.broker.rabbitmq import RabbitMQBroker, RabbitMQConsumer


@pytest.fixture(scope="function")
def broker():
    broker = RabbitMQBroker(
        f"test-{randint(1, 1000)}",
        {
            "host": "localhost",
            "port": 5672,
            "username": "admin",
            "password": "admin",
            "ssl": False,
        }
    )
    yield broker
    broker.client.shutdown()


def test_mq_stream_broker_consume(broker):
    messages = [
        ('{"a": "b"}', {"a": "b"}),
        ('123', 123),
        ('{"body": 123}', 123)
    ]

    for message in messages:
        broker.publish(message[0])

    consumer = broker.consume()
    assert isinstance(consumer, RabbitMQConsumer)
    for message in messages:
        result = next(consumer)
        assert isinstance(result, Message)
        assert result.body == message[1]
        result.broker = broker
        result.confirm()


@pytest.fixture
def queue():
    return Queue()


def test_mq_consume_multi_messages(broker):
    broker.prefetch = 5  # mock prefetch
    for _ in range(10):
        broker.publish('{"a": "b"}')

    consumer = broker.consume()
    time.sleep(1)
    assert consumer.queue.qsize() == broker.prefetch


def test_requeue(broker):
    broker.publish('{"body": {"a1": "b2"}}')
    consumer = broker.consume()

    assert isinstance(consumer, RabbitMQConsumer)
    result = next(consumer)
    broker.requeue(result, is_source=True)

    consumer = broker.consume()
    result = next(consumer)
    assert result.body['a1'] == 'b2'

    broker.requeue(result, is_source=False)

    consumer = broker.consume()
    result = next(consumer)
    assert result.body['a1'] == 'b2'
