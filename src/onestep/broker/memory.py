from queue import Queue, Empty

from .base import BaseBroker


class MemoryBroker(BaseBroker):

    def __init__(self, queue=None, *args, **kwargs):
        self.queue: Queue = queue or Queue()
        super().__init__(queue=self.queue, *args, **kwargs)

    def send(self, message):
        self.queue.put_nowait(message)

    def consume(self, *args, **kwargs):
        return MemoryConsumer(self.queue, *args, **kwargs)

    def nack(self, message, requeue=False):
        if requeue:
            self.queue.put_nowait(message.dict())


class MemoryConsumer:

    def __init__(self, queue: Queue, *args, **kwargs):
        self.queue = queue

    def __next__(self):
        try:
            return self.queue.get()
        except Empty:
            return None

    def __iter__(self):
        return self
