import json
import threading
from queue import Queue
from typing import Optional, Dict, Any

import amqpstorm

from .base import BaseBroker, BaseConsumer
from ..store.rabbitmq import RabbitMQStore
from ..message import Message


class RabbitMQBroker(BaseBroker):

    def __init__(self, queue_name, params: Optional[Dict] = None, prefetch: Optional[int] = 1, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.queue_name = queue_name
        self.queue = Queue()
        params = params or {}
        self.client = RabbitMQStore(**params)
        self.client.declare_queue(self.queue_name)
        self.prefetch = prefetch

    def _consume(self, *args, **kwargs):
        def callback(message: amqpstorm.Message):
            self.queue.put(message)

        prefetch = kwargs.pop("prefetch", self.prefetch)
        self.client.start_consuming(queue_name=self.queue_name, callback=callback, prefetch=prefetch, **kwargs)

    def consume(self, *args, **kwargs):
        threading.Thread(target=self._consume, *args, **kwargs).start()
        return RabbitMQConsumer(self.queue)

    def publish(self, message: Any):
        self.client.send(self.queue_name, message)

    def confirm(self, message: Message):
        """确认消息"""
        self.client.channel.basic.ack(message.msg.delivery_tag)

    def reject(self, message: Message):
        """拒绝消息"""
        self.client.channel.basic.nack(message.msg.delivery_tag, requeue=False)

    def requeue(self, message: Message, is_source=False):
        """
        重发消息：先拒绝 再 重入
        
        :param message: 消息
        :param is_source: 是否是源消息，True: 使用消息的最新数据重入当前队列，False: 使用消息的最新数据重入当前队列
        """
        if is_source:
            self.client.channel.basic.nack(message.msg.delivery_tag, requeue=True)
        else:
            self.client.channel.basic.nack(message.msg.delivery_tag, requeue=False)
            self.send(message)


class RabbitMQConsumer(BaseConsumer):
    def _to_message(self, data: amqpstorm.Message):
        try:
            message = json.loads(data.body)
        except json.JSONDecodeError:
            message = {"body": data.body}
        if not isinstance(message, dict):
            message = {"body": message}

        return Message(body=message.get("body"), extra=message.get("extra"), msg=data)
