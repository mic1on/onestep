from typing import Generator

import pytest

from onestep import MemoryBroker, BaseMiddleware, DropMessage
from onestep.message import Message
from onestep.onestep import SyncOneStep
from onestep.worker import BaseWorker, ThreadWorker


@pytest.fixture
def step():
    return SyncOneStep(fn=lambda message: message)


@pytest.fixture
def broker():
    return MemoryBroker()


@pytest.fixture
def worker(step, broker):
    wt = BaseWorker(step, broker)
    yield wt


@pytest.fixture
def thread_worker(step, broker):
    wt = ThreadWorker(step, broker)
    yield wt


def test_receive_messages_shutdown(worker, broker):
    worker._shutdown = True
    assert isinstance(worker.receive_messages(), Generator)


def test_receive_messages(worker, broker):
    broker.publish({"a": "b"})
    for message in worker.receive_messages():
        assert isinstance(message, Message)
        assert message.body == {"a": "b"}
        break


def test_receive_messages_while_once(thread_worker, broker):
    broker.publish({"a": "b"})
    broker.once = True
    for message in thread_worker.receive_messages():
        assert message.body == {"a": "b"}
    assert thread_worker._shutdown is True


def test_receive_messages_while_not_once(thread_worker, broker):
    broker.publish({"a": "b"})
    broker.once = False
    for message in thread_worker.receive_messages():
        assert message.body == {"a": "b"}
        break
    assert thread_worker._shutdown is False


def test_receive_messages_consume(thread_worker, broker):
    broker.publish({"a": "b"})
    for message in thread_worker.receive_messages():
        assert message.body == {"a": "b"}
        break


def test_handle_message_consume_exception(step, thread_worker, broker):
    class test_middleware(BaseMiddleware):
        def before_consume(self, step, message, *args, **kwargs):
            message.drop = True
            raise DropMessage('drop message')

    step.middlewares = [test_middleware()]
    message = Message(body={"a": "b"})
    thread_worker.handle_message(message)
    assert message.drop is True


def test_handle_message(step, thread_worker, broker):
    class test_middleware(BaseMiddleware):
        def before_consume(self, step, message, *args, **kwargs):
            assert message.body == {"a": "b"}

        def after_consume(self, step, message, *args, **kwargs):
            assert message.body == {"a": "c"}

    step.middlewares = [test_middleware()]
    message = Message(body={"a": "b"})
    step.fn = lambda msg: msg.body.update({"a": "c"})
    thread_worker.handle_message(message)


def test__run_real_instance(step, thread_worker, broker):
    step.fn = lambda msg: msg.body.update({"a": "c"})
    message = Message(body={"a": "b"})
    thread_worker._run_real_instance(message)
    assert message.body == {"a": "c"}
