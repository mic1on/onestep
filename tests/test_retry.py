from pytest import fixture
from onestep.message import Message
from onestep import NeverRetry, AlwaysRetry, TimesRetry, RetryIfException, RetryInQueue
from onestep.retry import RetryStatus, AdvancedRetry


@fixture
def message():
    return Message({"a": 1})


def test_never_retry(message):
    retry_class = NeverRetry()
    assert RetryStatus.END_WITH_CALLBACK is retry_class(message)


def test_always_retry(message):
    retry_class = AlwaysRetry()
    assert RetryStatus.CONTINUE is retry_class(message)


def test_times_retry(message):
    retry_class = TimesRetry(3)
    assert RetryStatus.CONTINUE is retry_class(message)
    message.failure_count = 3
    assert RetryStatus.END_WITH_CALLBACK is retry_class(message)


def test_exception_retry2(message):
    retry_class = RetryIfException((ZeroDivisionError,))
    try:
        1 / 0
    except Exception:
        message.set_exception()
        assert RetryStatus.CONTINUE is retry_class(message)


def test_AdvancedRetry(message):
    retry_class = AdvancedRetry(times=3)
    try:
        1 / 0
    except Exception:
        message.set_exception()
    assert RetryStatus.END_WITH_CALLBACK is retry_class(message)

    retry_class = AdvancedRetry(times=3, exceptions=(ZeroDivisionError,))
    try:
        1 / 0
    except Exception:
        message.set_exception()
    assert RetryStatus.CONTINUE is retry_class(message)
    message.failure_count = 4
    assert RetryStatus.END_WITH_CALLBACK is retry_class(message)
    message.failure_count = 3
    assert RetryStatus.END_WITH_CALLBACK is retry_class(message)
    message.failure_count = 1
    assert RetryStatus.CONTINUE is retry_class(message)
    try:
        raise RetryInQueue()
    except Exception:
        message.set_exception()
    assert message.failure_count == 2
    assert RetryStatus.END_IGNORE_CALLBACK is retry_class(message)
