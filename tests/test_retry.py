from pytest import fixture
from onestep.message import Message
from onestep import NeverRetry, AlwaysRetry, TimesRetry, RetryIfException


@fixture
def message():
    return Message({"a": 1})


def test_never_retry(message):
    retry_class = NeverRetry()
    assert False is retry_class(message)


def test_always_retry(message):
    retry_class = AlwaysRetry()
    assert True is retry_class(message)


def test_times_retry(message):
    retry_class = TimesRetry(3)
    assert True is retry_class(message)
    message.failure_count = 3
    assert False is retry_class(message)



def test_exception_retry2(message):
    retry_class = RetryIfException((ZeroDivisionError,))
    try:
        1 / 0
    except Exception as e:
        message.set_exception()
        assert True is retry_class(message)
