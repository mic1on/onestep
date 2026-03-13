import pytest

from onestep import step
from onestep.broker import MemoryBroker


@pytest.fixture
def broker():
    return MemoryBroker()


def test_func(broker):

    @step(from_broker=broker, name="do_some_thing")
    def do_some_thing(message):
        ...
    assert do_some_thing.__step_params__ is not None

    assert do_some_thing.__step_params__["from_broker"] == broker
    assert do_some_thing.__step_params__["name"] == "do_some_thing"