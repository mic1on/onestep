import collections
import pytest
from onestep.broker.redis import RedisStreamBroker, RedisStreamConsumer


@pytest.fixture
def broker():
    return RedisStreamBroker(
        stream='test_stream',
        group='test_group',
        consumer='test_consumer',
        params={
            "password": "123456"
        }
    )


# RedisStreamBroker测试用例
def test_redis_stream_broker(broker):
    assert broker.client.connection.ping()

    broker.publish('{"body": {"a": "b"}}')

    # assert broker.client.connection.xlen('test_stream') == 1


def test_redis_stream_broker_consume(broker):
    consumer = broker.consume()
    assert isinstance(consumer, RedisStreamConsumer)
    data = consumer.queue.get()
    result = consumer._to_message(data)
    assert isinstance(result, collections.Iterable)
    message = next(result)
    assert message.body == {'a': 'b'}
