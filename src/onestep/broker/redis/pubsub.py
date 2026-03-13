import json
import threading
from queue import Queue
from typing import Any, List, Dict, Optional

try:
    from use_redis import useRedis
except ImportError:
    useRedis = None

from ..base import BaseBroker, BaseConsumer, Message


class _RedisPubSubMessage(Message):

    @classmethod
    def from_broker(cls, broker_message: Any):
        if isinstance(broker_message, dict) and "channel" in broker_message:
            try:
                data = broker_message.get("data")
                if data is not None:
                    message = json.loads(data)  # 已转换的 message
                else:
                    message = {"body": None}
            except (json.JSONDecodeError, TypeError):
                message = {"body": broker_message.get("data")}  # 未转换的 message
        else:
            # 来自 外部的消息 直接认为都是 message.body
            message = {"body": broker_message.body}

        yield cls(body=message.get("body"), extra=message.get("extra"), message=broker_message)


class RedisPubSubBroker(BaseBroker):
    """ Redis PubSub Broker """
    message_cls = _RedisPubSubMessage

    def __init__(self, channel: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.channel = channel
        self.queue: Queue = Queue()

        self.threads: List[threading.Thread] = []

        self.client = useRedis(**kwargs).connection

    def _consume(self) -> None:
        def callback(message: Dict[str, Any]) -> None:
            if message.get('type') != 'message':
                return
            if self.queue is not None:
                self.queue.put(message)

        ps = self.client.pubsub()
        ps.subscribe(self.channel)
        for message in ps.listen():
            callback(message)

    def consume(self, *args, **kwargs):
        daemon = kwargs.pop('daemon', True)
        thread = threading.Thread(target=self._consume, daemon=daemon)
        thread.start()
        self.threads.append(thread)
        return RedisPubSubConsumer(self)

    def send(self, message: Any, *args, **kwargs):
        """Publish message to the Redis channel"""
        if not isinstance(message, Message):
            message = self.message_cls(body=message)

        self.client.publish(self.channel, message.to_json(), *args, **kwargs)

    publish = send

    def confirm(self, message: Message):
        pass

    def reject(self, message: Message):
        pass

    def requeue(self, message: Message, is_source=False):
        """
         重发消息：先拒绝 再 重入

         :param message: 消息
         :param is_source: 是否是原始消息消息，True: 使用原始消息重入当前队列，False: 使用消息的最新数据重入当前队列
         """
        self.reject(message)

        if is_source and isinstance(message.message, dict):
            data = message.message.get('data')
            if data is not None:
                self.client.publish(self.channel, data)
        else:
            self.send(message)


class RedisPubSubConsumer(BaseConsumer):
    ...
