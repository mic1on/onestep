import time
import json

import pytest

from onestep.broker.sqs.sqs import SQSBroker, SQSStore as RealSQSStore
from onestep.message import Message


class FakeMessage:
    def __init__(self, body, message_id=None):
        self.body = body if isinstance(body, str) else json.dumps(body)
        self.message_id = message_id or "msg-1"
        self.deleted = False

    def delete(self):
        self.deleted = True


class FakeSQSStore:
    def __init__(self, **kwargs):
        self._shutdown = False
        self._messages = []

    def declare_queue(self, queue_name, attributes=None, auto_create=True):
        return queue_name

    def send(self, queue_name, message, message_group_id=None, **kwargs):
        body = message if isinstance(message, (str, bytes)) else json.dumps(message)
        self._messages.append(FakeMessage(body))
        return message

    def start_consuming(self, queue_name, callback, prefetch=1, **kwargs):
        self._shutdown = False
        while not self._shutdown:
            count = 0
            while self._messages and count < prefetch:
                count += 1
                msg = self._messages.pop(0)
                callback(msg)
            time.sleep(0.05)

    def shutdown(self):
        self._shutdown = True


@pytest.fixture(autouse=True)
def patch_store(monkeypatch):
    monkeypatch.setattr("onestep.broker.sqs.sqs.SQSStore", FakeSQSStore)
    yield
    monkeypatch.setattr("onestep.broker.sqs.sqs.SQSStore", RealSQSStore)


def test_sqs_consume_and_confirm():
    broker = SQSBroker(queue_name="test-q.fifo", message_group_id="group", prefetch=2)
    broker.publish({"body": {"a": 2}})
    consumer = broker.consume()
    msg: Message = next(consumer)
    assert isinstance(msg, Message)
    assert msg.body == {"a": 2}
    broker.confirm(msg)
    broker.shutdown()


def test_sqs_requeue_source_and_current():
    broker = SQSBroker(queue_name="test-q.fifo", message_group_id="group", prefetch=1)
    broker.publish({"body": {"k": "v"}})
    consumer = broker.consume()
    msg = next(consumer)

    broker.requeue(msg, is_source=True)
    consumer2 = broker.consume()
    msg2 = next(consumer2)
    assert msg2.body == {"k": "v"}

    msg2.body = {"k": "v2"}
    broker.requeue(msg2, is_source=False)
    consumer3 = broker.consume()
    msg3 = next(consumer3)
    assert msg3.body == {"k": "v2"}
    broker.shutdown()


def test_sqs_single_consuming_thread():
    broker = SQSBroker(queue_name="test-q.fifo", message_group_id="group", prefetch=1)
    c1 = broker.consume()
    broker.consume()
    assert len(broker.threads) == 1
    broker.publish({"body": 123})
    time.sleep(0.1)
    assert c1.queue.qsize() >= 1
    broker.shutdown()


def test_sqs_prefetch():
    broker = SQSBroker(queue_name="test-q.fifo", message_group_id="group", prefetch=5)
    for i in range(10):
        broker.publish({"body": i})
    consumer = broker.consume()
    time.sleep(0.2)
    assert consumer.queue.qsize() >= 5
    broker.shutdown()


if __name__ == "__main__":
    test_sqs_consume_and_confirm()
