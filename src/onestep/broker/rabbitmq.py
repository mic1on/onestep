import threading
from queue import Queue, Empty
from typing import Optional, Dict

from .base import BaseBroker
from .store.rabbitmq import RabbitmqStore


class RabbitMQBroker(BaseBroker):

    def __init__(self, queue_name, params: Optional[Dict] = None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.queue_name = queue_name
        self.queue = Queue()
        params = params or {}
        self.client = RabbitmqStore(**params)
        self.client.declare_queue(self.queue_name)

    def _consume(self):
        def callback(message):
            self.queue.put(message)

        self.client.start_consuming(self.queue_name, callback)

    def consume(self, *args, **kwargs):
        threading.Thread(target=self._consume).start()
        return RabbitMQConsumer(self.queue)

    def send(self, message):
        self.client.send(self.queue_name, message.message)

    @staticmethod
    def ack(message):
        message.message.ack()

    @staticmethod
    def nack(message, requeue=False):
        message.message.nack(requeue=requeue)


class RabbitMQConsumer:

    def __init__(self, queue, *args, **kwargs):
        self.queue = queue
        self.timeout = kwargs.pop("timeout", 1000)

    def __next__(self):
        try:
            return self.queue.get(timeout=self.timeout / 1000)
        except Empty:
            return None

    def __iter__(self):  # pragma: no cover
        return self
