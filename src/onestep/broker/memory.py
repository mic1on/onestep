import logging
from queue import Queue, Full as FullException
from typing import Any

from .base import BaseBroker, BaseConsumer

from ..message import Message

logger = logging.getLogger(__name__)


class MemoryBroker(BaseBroker):

    def __init__(self, maxsize=0, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.queue = Queue(maxsize)

    def publish(self, message: Any):
        try:
            self.queue.put_nowait(message)
        except FullException:
            logger.warning("CronBroker queue is full, skip this task, "
                           "you can increase maxsize with `maxsize` argument")

    def consume(self, *args, **kwargs):
        return MemoryConsumer(self, *args, **kwargs)

    def confirm(self, message: Message):
        """确认消息"""
        pass

    def reject(self, message: Message):
        """拒绝消息"""
        pass

    def requeue(self, message: Message, is_source=False):
        """重发消息：先拒绝 再 重入"""
        if is_source:
            self.publish(message.message)
        else:
            self.send(message)

    def __repr__(self):
        return f"<{self.__class__.__name__} {self.name}>"

    def __str__(self):
        return self.name


class MemoryConsumer(BaseConsumer):
    ...
