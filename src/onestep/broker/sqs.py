import json
import threading
from queue import Queue
from typing import Optional, Dict, Any

import boto3
from botocore.exceptions import ClientError

from .base import BaseBroker, BaseConsumer
from ..message import Message


class SQSBroker(BaseBroker):

    def __init__(self, queue_name, message_group_id, params: Optional[Dict] = None, prefetch: Optional[int] = 1,
                 auto_create=True, queue_params: Optional[Dict] = None,
                 *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.queue_name = queue_name
        self.queue = Queue()
        params = params or {}
        self.client = boto3.resource("sqs", **params)
        self.sqs_queue = self._get_or_create_queue(queue_name, auto_create, queue_params)
        self.message_group_id = message_group_id
        self.prefetch = prefetch
        self.threads = []

    def _get_or_create_queue(self, queue_name: str, auto_create: bool, queue_params: Optional[Dict] = None):
        _queue = None
        try:
            _queue = self.client.get_queue_by_name(QueueName=queue_name)
        except ClientError:
            ...
        if not _queue and auto_create:
            _queue = self.client.create_queue(QueueName=queue_name, Attributes=queue_params)
        return _queue

    def _consume(self, *args, **kwargs):
        prefetch = kwargs.pop("prefetch", self.prefetch)
        while True:
            messages = self.sqs_queue.receive_messages(AttributeNames=["All"],
                                                       MaxNumberOfMessages=prefetch,
                                                       **kwargs)
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
        """发布消息"""
        self.sqs_queue.send_message(MessageBody=message, MessageGroupId=self.message_group_id)

    def confirm(self, message: Message):
        """确认消息"""
        message.msg.delete()

    def reject(self, message: Message):
        """拒绝消息"""
        message.msg.delete()

    def requeue(self, message: Message, is_source=False):
        """
        重发消息：先拒绝 再 重入

        :param message: 消息
        :param is_source: 是否是原始消息，True: 使用原始消息重入当前队列，False: 使用消息的最新数据重入当前队列
        """
        if is_source:
            message.msg.delete()
            self.send(message.msg)
        else:
            message.msg.delete()
            self.send(message)

    def shutdown(self):
        for thread in self.threads:
            thread.join()


class SQSConsumer(BaseConsumer):
    def _to_message(self, data):
        try:
            message = json.loads(data.body)
        except json.JSONDecodeError:
            message = {"body": data.body}
        if not isinstance(message, dict):
            message = {"body": message}
        if "body" not in message:
            # 来自 外部的消息 可能没有 body, 故直接认为都是 message.body
            message = {"body": message}

        return Message(body=message.get("body"), extra=message.get("extra"), msg=data)
