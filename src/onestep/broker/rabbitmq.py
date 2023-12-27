import json
import threading
from queue import Queue
from typing import Optional, Dict, Any

import amqpstorm

from .base import BaseBroker, BaseConsumer
from use_rabbitmq import useRabbitMQ as RabbitMQStore
from ..message import Message


class _RabbitMQMessage(Message):

    @classmethod
    def from_broker(cls, broker_message: amqpstorm.Message):
        try:
            message = json.loads(broker_message.body)
        except json.JSONDecodeError:
            message = {"body": broker_message.body}
        if not isinstance(message, dict):
            message = {"body": message}
        if "body" not in message:
            # 来自 外部的消息 可能没有 body, 故直接认为都是 message.body
            message = {"body": message}

        return cls(body=message.get("body"), extra=message.get("extra"), message=broker_message)


class RabbitMQBroker(BaseBroker):
    message_cls = _RabbitMQMessage

    def __init__(self, queue_name, params: Optional[Dict] = None, prefetch: Optional[int] = 1, auto_create=True, *args,
                 **kwargs):
        super().__init__(*args, **kwargs)
        self.queue_name = queue_name
        self.queue = Queue()
        params = params or {}
        self.client = RabbitMQStore(**params)
        if auto_create:
            self.client.declare_queue(self.queue_name)
        self.prefetch = prefetch
        self.threads = []

    def _consume(self, *args, **kwargs):
        def callback(message: amqpstorm.Message):
            self.queue.put(message)

        prefetch = kwargs.pop("prefetch", self.prefetch)
        self.client.start_consuming(queue_name=self.queue_name, callback=callback, prefetch=prefetch, **kwargs)

    def consume(self, *args, **kwargs):
        daemon = kwargs.pop('daemon', True)
        thread = threading.Thread(target=self._consume, *args, **kwargs)
        thread.daemon = daemon
        thread.start()
        self.threads.append(thread)
        return RabbitMQConsumer(self)

    def publish(self, message: Any):
        self.client.send(self.queue_name, message)

    def confirm(self, message: Message):
        """确认消息"""
        message.message.ack()

    def reject(self, message: Message):
        """拒绝消息"""
        message.message.reject(requeue=False)

    def requeue(self, message: Message, is_source=False):
        """
        重发消息：先拒绝 再 重入
        
        :param message: 消息
        :param is_source: 是否是原始消息，True: 使用原始消息重入当前队列，False: 使用消息的最新数据重入当前队列
        """
        if is_source:
            message.message.reject(requeue=True)
        else:
            message.message.reject(requeue=False)
            self.send(message)

    def shutdown(self):
        self.client.shutdown()
        for thread in self.threads:
            thread.join()


class RabbitMQConsumer(BaseConsumer):
    ...
