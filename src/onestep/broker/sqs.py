import threading
from queue import Queue
from typing import Optional, Dict, Any

import boto3

from .base import BaseBroker, BaseConsumer
from ..message import Message


class SQSBroker(BaseBroker):

    def __init__(self, queue_name, params: Optional[Dict] = None, prefetch: Optional[int] = 1, auto_create=True, *args,
                 **kwargs):
        super().__init__(*args, **kwargs)
        self.queue_name = queue_name
        self.queue = Queue()
        params = params or {}
        self.client = boto3.client("sqs", **params)
        self.sqs_queue = self._get_or_create_queue(queue_name, auto_create)
        self.prefetch = prefetch
        self.threads = []

    def _get_or_create_queue(self, queue_name: str, auto_create: bool):
        return self.client.get_queue_by_name(QueueName=queue_name)
        # TODO check exist
        if auto_create:
            sqs_queue = self.client.create_queue(QueueName=queue_name, Attributes=kwargs)
        return sqs_queue

    def _consume(self, *args, **kwargs):
        prefetch = kwargs.pop("prefetch", self.prefetch)
        messages = self.sqs_queue.receive_messages(MaxNumberOfMessages=prefetch, **kwargs)
        for msg in messages:
            self.queue.put(msg)

    def consume(self, *args, **kwargs):
        daemon = kwargs.pop('daemon', True)
        thread = threading.Thread(target=self._consume, *args, **kwargs)
        thread.daemon = daemon
        thread.start()
        self.threads.append(thread)
        return SQSConsumer(self.queue)

    def publish(self, message: Any):
        self.sqs_queue.send_message(MessageBody=message)

    def confirm(self, message: Message):
        """确认消息"""
        message.delete()

    def reject(self, message: Message):
        """拒绝消息"""
        message.delete()

    def requeue(self, message: Message, is_source=False):
        """
        重发消息：先拒绝 再 重入

        :param message: 消息
        :param is_source: 是否是原始消息，True: 使用原始消息重入当前队列，False: 使用消息的最新数据重入当前队列
        """
        if is_source:
            message.delete()
            self.send(message.msg)
        else:
            message.delete()
            self.send(message)

    def shutdown(self):
        self.client.shutdown()
        for thread in self.threads:
            thread.join()


class SQSConsumer(BaseConsumer):
    def _to_message(self, data):
        pass
