import time

import pytest
from onestep.broker.redis import RedisStreamBroker, RedisStreamConsumer

try:
    from collections import Iterable
except ImportError:
    from collections.abc import Iterable


@pytest.fixture
def broker():
    return RedisStreamBroker(stream="test_stream", group="test_group")


def test_send_and_consume(broker):
    broker.client.connection.flushall()  # 清空测试数据

    message_body = {"data": "Test message"}
    broker.send(message_body)

    consumer = broker.consume()
    assert isinstance(consumer, RedisStreamConsumer)
    received_message = next(next(consumer))  # noqa
    assert received_message.body == message_body
    broker.client.shutdown()


def test_redis_consume_multi_messages(broker):
    broker.client.connection.flushall()  # 清空测试数据

    broker.prefetch = 2  # mock prefetch
    broker.send({"body": {"a1": "b1"}})
    broker.send({"body": {"a2": "b2"}})

    consumer = broker.consume()
    time.sleep(3)  # 等待消息取到本地
    assert consumer.queue.qsize() == 2  # Ensure that 2 messages are received
    broker.client.shutdown()


def test_confirm_reject(broker):
    broker.client.connection.flushall()  # 清空测试数据

    message_body = "Test message"
    broker.send(message_body)

    consumer = broker.consume()
    received_message = next(next(consumer))  # noqa

    broker.confirm(received_message)
    assert next(consumer) is None

    broker.send(message_body)
    received_message = next(next(consumer))  # noqa

    broker.reject(received_message)
    assert next(consumer) is None
    broker.client.shutdown()


def test_requeue(broker):
    broker.client.connection.flushall()  # 清空测试数据

    message_body = "Test message"
    broker.send(message_body)

    consumer = broker.consume()
    received_message = next(next(consumer))  # noqa

    broker.requeue(received_message)
    time.sleep(2)  # 等待消息取到本地
    requeued_message = next(next(consumer))
    assert requeued_message.body == message_body
    broker.client.shutdown()
