import pytest

from onestep import DropMessage
from onestep.message import Message

from onestep.middleware import MemoryUniqueMiddleware


@pytest.fixture
def onestep():
    from onestep import step
    return step


def test_has_seen():
    mum = MemoryUniqueMiddleware()
    assert mum.has_seen("123") is False
    mum.mark_seen("123")
    assert mum.has_seen("123") is True


def test_unique_middleware():
    msg = Message(body="123")

    mum = MemoryUniqueMiddleware()
    mum.before_consume(None, msg)
    mum.after_consume(None, msg)

    try:
        mum.before_consume(None, msg)
    except Exception as e:
        assert isinstance(e, DropMessage)
