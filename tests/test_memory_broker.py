import json

import pytest
from onestep.broker import MemoryBroker

message = "message1"


@pytest.fixture
def broker():
    return MemoryBroker()


@pytest.fixture
def consumer(broker):
    broker.send(message)
    return broker.consume()


def test_memory_broker_send(broker, consumer):
    # broker.queue.get() 拿到的是原始消息字符串，一般不需要直接使用
    reached_message = broker.queue.get()
    assert json.loads(reached_message).get("body") == message


def test_memory_broker_consume(broker, consumer):
    # broker.consume() 拿到的是 Message 对象
    consumer = broker.consume()
    assert next(consumer).body == message


def test_memory_broker_reject(broker, consumer):
    msg = next(consumer)
    broker.reject(msg)
    assert next(consumer) is None


def test_memory_broker_requeue_source(broker, consumer):
    msg = next(consumer)
    msg.body = "message2"
    broker.requeue(msg, is_source=True)  # 原始消息重新入队
    assert next(consumer).body == message


def test_memory_broker_requeue_not_source(broker, consumer):
    msg = next(consumer)
    msg.body = "message2"
    broker.requeue(msg, is_source=False)  # 修改后的消息重新入队
    assert next(consumer).body == "message2"
