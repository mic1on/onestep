import json

import pytest

from onestep.broker import MemoryBroker
from onestep.middleware import BaseMiddleware
from onestep.onestep import SyncOneStep
from onestep.worker import ThreadWorker
from onestep.message import Message


class BrokerTestMiddleware(BaseMiddleware):
    def __init__(self):
        self.calls = []

    def before_send(self, step, message, *args, **kwargs):
        self.calls.append("before_send")

    def after_send(self, step, message, *args, **kwargs):
        self.calls.append("after_send")

    def before_consume(self, step, message, *args, **kwargs):
        self.calls.append("before_consume")
        message.broker_mark_consume = True

    def after_consume(self, step, message, *args, **kwargs):
        self.calls.append("after_consume")

    def before_confirm(self, step, message, *args, **kwargs):
        self.calls.append("before_confirm")

    def after_confirm(self, step, message, *args, **kwargs):
        self.calls.append("after_confirm")


@pytest.fixture
def broker():
    m = BrokerTestMiddleware()
    b = MemoryBroker(middlewares=[m])
    return b, m


def test_broker_send_triggers(broker):
    b, m = broker
    b.send({"a": 1})
    _ = b.queue.get()
    assert "before_send" in m.calls and "after_send" in m.calls


def test_broker_consume_triggers(broker):
    b, m = broker
    b.publish({"a": 2})

    step = SyncOneStep(fn=lambda msg: None)
    worker = ThreadWorker(step, b)
    msg = next(b.consume())
    worker.handle_message(msg)
    assert "before_consume" in m.calls and "after_consume" in m.calls
    assert getattr(msg, "broker_mark_consume", False) is True


def test_broker_confirm_triggers(broker):
    b, m = broker
    b.publish({"a": 3})
    msg = next(b.consume())
    msg.broker = b
    msg.confirm()
    assert "before_confirm" in m.calls and "after_confirm" in m.calls
