import logging
import json
from queue import Queue, Full as FullException
from typing import Any

from .base import BaseBroker, BaseConsumer

from ..message import Message

logger = logging.getLogger(__name__)


class MemoryMessage(Message):

    @classmethod
    def from_broker(cls, broker_message: Any):
        if isinstance(broker_message, (str, bytes, bytearray)):
            try:
                message = json.loads(broker_message)
            except json.JSONDecodeError:
                message = {"body": broker_message}
        else:
            message = broker_message
        if not isinstance(message, dict):
            message = {"body": message}
        if "body" not in message:
            # 来自 外部的消息 可能没有 body, 故直接认为都是 message.body
            message = {"body": message}
        return cls(body=message.get("body"), extra=message.get("extra"), message=broker_message)


class MemoryBroker(BaseBroker):
    message_cls = MemoryMessage

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
