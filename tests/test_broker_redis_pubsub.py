import pytest
import threading
import time

from onestep.broker.redis.pubsub import RedisPubSubConsumer

from onestep import RedisPubSubBroker
from onestep.message import Message


# Assuming `your_module` is the module where your broker implementation resides.

@pytest.fixture
def broker():
    # Assuming you have a Redis server running locally on the default port.
    broker = RedisPubSubBroker(channel="test_channel")
    yield broker
    broker.client.close()  # Cleanup after the test


def test_publish_and_consume(broker):
    consumer = broker.consume(daemon=True)

    message_to_publish = "Test Message"
    broker.publish(message_to_publish)

    time.sleep(1)

    received_message = next(next(consumer))
    assert isinstance(received_message, Message)
    assert received_message.body == message_to_publish


def test_redis_consume_multi_messages(broker):
    broker.prefetch = 2  # mock prefetch
    consumer = broker.consume()  # must consume before send, because the message will be lost

    broker.send({"body": {"a1": "b1"}})
    broker.send({"body": {"a2": "b2"}})

    time.sleep(3)  # 等待消息取到本地
    assert consumer.queue.qsize() == 2  # Ensure that 2 messages are received


def test_requeue(broker):
    consumer = broker.consume()
    message_body = "Test message"
    broker.send(message_body)

    received_message = next(next(consumer))  # noqa

    broker.requeue(received_message)
    time.sleep(2)  # 等待消息取到本地
    requeued_message = next(next(consumer))
    assert requeued_message.body == message_body
