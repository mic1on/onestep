import pytest
from urllib import request

from onestep import WebHookBroker


@pytest.fixture
def webhook_broker():
    broker = WebHookBroker("/push", port=8888)
    yield broker
    broker.shutdown()


def test_webhook_broker(webhook_broker):
    consumer = webhook_broker.consume()

    url = "http://localhost:8888/push"
    data = b'{"message": "Test webhook"}'
    req = request.Request(url, data=data, headers={'Content-Type': 'application/json'})
    response = request.urlopen(req)

    assert response.getcode() == 200

    assert consumer.queue.qsize() == 1
    assert consumer.queue.get() == data.decode("utf-8")
