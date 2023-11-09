import json
import threading
import uuid
from queue import Queue
from typing import Optional, Dict, Any

try:
    from usepy_plugin_redis import useRedisStreamStore, RedisStreamMessage
except ImportError:
    ...

from .base import BaseBroker, BaseConsumer, Message


class RedisStreamBroker(BaseBroker):
    """ Redis Stream Broker """

    def __init__(
            self,
            stream: str,
            group: str = "onestep",
            params: Optional[Dict] = None,
            prefetch: Optional[int] = 1,
            stream_max_entries: int = 0,
            redeliver_timeout: int = 60000,
            claim_interval: int = 1800000,
            *args,
            **kwargs):
        super().__init__(*args, **kwargs)
        self.stream = stream
        self.group = group
        self.prefetch = prefetch
        self.queue = Queue()

        self.threads = []

        self.client = useRedisStreamStore(
            stream=stream,
            group=group,
            stream_max_entries=stream_max_entries,
            redeliver_timeout=redeliver_timeout,
            claim_interval=claim_interval,
            **(params or {})
        )

    def _consume(self, *args, **kwargs):
        def callback(message):
            self.queue.put(message)

        prefetch = kwargs.pop("prefetch", self.prefetch)
        self.client.start_consuming(consumer=uuid.uuid4().hex, callback=callback, prefetch=prefetch, **kwargs)

    def consume(self, *args, **kwargs):
        daemon = kwargs.pop('daemon', True)
        thread = threading.Thread(target=self._consume, *args, **kwargs)
        thread.daemon = daemon
        thread.start()
        self.threads.append(thread)
        return RedisStreamConsumer(self.queue)

    def send(self, message: Any):
        """对消息进行预处理，然后再发送"""
        if not isinstance(message, Message):
            message = Message(body=message)

        self.client.send({"_message": message.to_json()})

    publish = send

    def confirm(self, message: Message):
        self.client.ack(message.msg)

    def reject(self, message: Message):
        self.client.reject(message.msg)

    def requeue(self, message: Message, is_source=False):
        """
         重发消息：先拒绝 再 重入
 
         :param message: 消息
         :param is_source: 是否是原始消息消息，True: 使用原始消息重入当前队列，False: 使用消息的最新数据重入当前队列
         """
        self.reject(message)

        if is_source:
            self.client.send(message.msg.body)
        else:
            self.send(message)


class RedisStreamConsumer(BaseConsumer):
    def _to_message(self, data: "RedisStreamMessage"):
        if "_message" in data.body:
            # 来自 RedisStreamBroker.send 的消息，message.body 默认是存于 _message 字段中
            try:
                message = json.loads(data.body.get("_message"))  # 已转换的 message
            except (json.JSONDecodeError, TypeError):
                message = {"body": data.body.get("_message")}  # 未转换的 message
        else:
            # 来自 外部的消息 直接认为都是 message.body
            message = {"body": data.body}

        yield Message(body=message.get("body"), extra=message.get("extra"), msg=data)
