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
    message.fail_number = 3
    assert False is retry_class(message)


def test_exception_retry(message):
    retry_class = RetryIfException()
    assert False is retry_class(message)
    message.set_exception(ZeroDivisionError())
    assert True is retry_class(message)


def test_exception_retry2(message):
    retry_class = RetryIfException((ZeroDivisionError,))
    assert False is retry_class(message)
    message.set_exception(ValueError())
    assert False is retry_class(message)
    try:
        1 / 0
    except Exception as e:
        message.set_exception(e)
        assert True is retry_class(message)
