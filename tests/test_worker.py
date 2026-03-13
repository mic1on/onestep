import pytest

from onestep import MemoryBroker
from onestep.onestep import BaseOneStep
from onestep.worker import ThreadWorker


@pytest.fixture
def broker():
    return MemoryBroker()


@pytest.fixture
def worker_thread(broker):
    onestep = BaseOneStep(fn=lambda message: message)
    wt = ThreadWorker(onestep, broker)
    wt.start()
    yield wt


def test_shutdown(worker_thread):
    worker_thread.shutdown()
    assert worker_thread._shutdown is True


def test_run_loop(worker_thread):
    worker_thread.shutdown()
    worker_thread.run()
    worker_thread.run()
    worker_thread.run()
    assert worker_thread._shutdown is True


def test_broker_once(worker_thread):
    worker_thread.broker.once = True
    worker_thread.broker.publish("test")
    worker_thread.run()
    assert worker_thread._shutdown is True
