import json
from queue import Queue

from .base import BaseBroker, BaseConsumer
from ..message import Message


class MemoryBroker(BaseBroker):

    def __init__(self, queue=None, *args, **kwargs):
        self.queue: Queue = queue or Queue()
        super().__init__(queue=self.queue, *args, **kwargs)

    def consume(self, *args, **kwargs):
        return MemoryConsumer(self.queue, *args, **kwargs)

    def publish(self, message):
        self.queue.put_nowait(message)

    def nack(self, message, requeue=False):
        if requeue:
            self.queue.put_nowait(message.dict())


class MemoryConsumer(BaseConsumer):

    def _to_message(self, data: str):
        message = json.loads(data)
        return Message(body=message.get("body"), extra=message.get("extra"), msg=None)
