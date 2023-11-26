import json
import threading
from queue import Queue
from typing import Any

try:
    from usepy_plugin_redis import useRedis
except ImportError:
    ...

from ..base import BaseBroker, BaseConsumer, Message


class RedisPubSubBroker(BaseBroker):
    """ Redis PubSub Broker """

    def __init__(self, channel: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.channel = channel
        self.queue = Queue()

        self.threads = []

        self.client = useRedis(**kwargs).connection

    def _consume(self):
        def callback(message: dict):
            if message.get('type') != 'message':
                return
            self.queue.put(message)

        ps = self.client.pubsub()
        ps.subscribe(self.channel)
        for message in ps.listen():
            callback(message)

    def consume(self, *args, **kwargs):
        daemon = kwargs.pop('daemon', True)
        thread = threading.Thread(target=self._consume, *args, **kwargs)
        thread.daemon = daemon
        thread.start()
        self.threads.append(thread)
        return RedisPubSubConsumer(self.queue)

    def send(self, message: Any):
        """Publish message to the Redis channel"""
        if not isinstance(message, Message):
            message = Message(body=message)

        print(self.client.publish(self.channel, message.to_json()))

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

        if is_source:
            self.client.publish(self.channel, message.msg['data'])
        else:
            self.send(message)


class RedisPubSubConsumer(BaseConsumer):
    def _to_message(self, data):
        if "channel" in data:
            try:
                message = json.loads(data.get("data"))  # 已转换的 message
            except (json.JSONDecodeError, TypeError):
                message = {"body": data.get("data")}  # 未转换的 message
        else:
            # 来自 外部的消息 直接认为都是 message.body
            message = {"body": data.body}

        yield Message(body=message.get("body"), extra=message.get("extra"), msg=data)
