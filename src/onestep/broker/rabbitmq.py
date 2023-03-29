import json
import threading
from queue import Queue
from typing import Optional, Dict

import amqpstorm

from .base import BaseBroker, BaseConsumer
from ..store.rabbitmq import RabbitmqStore
from ..message import Message


class RabbitMQBroker(BaseBroker):

    def __init__(self, queue_name, params: Optional[Dict] = None, prefetch: Optional[int] = 1, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.queue_name = queue_name
        self.queue = Queue()
        params = params or {}
        self.client = RabbitmqStore(**params)
        self.client.declare_queue(self.queue_name)
        self.prefetch = prefetch

    def _consume(self, *args, **kwargs):
        def callback(message):
            self.queue.put(message)

        prefetch = kwargs.pop("prefetch", self.prefetch)
        self.client.start_consuming(queue_name=self.queue_name, callback=callback, prefetch=prefetch, **kwargs)

    def consume(self, *args, **kwargs):
        threading.Thread(target=self._consume, *args, **kwargs).start()
        return RabbitMQConsumer(self.queue)

    def publish(self, message):
        self.client.send(self.queue_name, message)

    @staticmethod
    def ack(message):
        message.msg.ack()

    @staticmethod
    def nack(message, requeue=False):
        message.msg.nack(requeue=requeue)


class RabbitMQConsumer(BaseConsumer):
    def _to_message(self, data: amqpstorm.Message):
        try:
            message = json.loads(data.body)
        except json.JSONDecodeError:
            message = {"body": data.body}
        if not isinstance(message, dict):
            message = {"body": message}

        return Message(body=message.get("body"), extra=message.get("extra"), msg=data)
