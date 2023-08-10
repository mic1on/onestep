import json
import threading
from typing import Optional, Dict
from queue import Queue

from usepy_plugin_redis import useRedisStreamStore

from .base import BaseBroker, BaseConsumer, Message


class RedisStreamBroker(BaseBroker):
    """ Redis Stream Broker """

    def __init__(self, stream, group, consumer: Optional[str] = None, params: Optional[Dict] = None,
                 prefetch: Optional[int] = 1, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.stream = stream
        self.group = group
        self.consumer = consumer or self.name
        self.prefetch = prefetch
        self.queue = Queue()
        self.client = useRedisStreamStore(**(params or {}))

    def _consume(self, *args, **kwargs):
        def callback(message):
            self.queue.put(message)

        prefetch = kwargs.pop("prefetch", self.prefetch)
        self.client.start_consuming(
            stream=self.stream, group=self.group, consumer=self.consumer,
            callback=callback, prefetch=prefetch, **kwargs
        )

    def consume(self, *args, **kwargs):
        threading.Thread(target=self._consume, *args, **kwargs).start()
        return RedisStreamConsumer(self.queue)

    def publish(self, message):
        self.client.send(self.stream, {"_message": message})

    def confirm(self, message: Message):
        message_id, _ = message.msg
        self.client.connection.xack(self.stream, self.group, message_id)

    def reject(self, message: Message):
        pass

    def requeue(self, message: Message, is_source=False):
        if is_source:
            _, message_body = message.msg
            self.client.send(self.stream, message_body)
        else:
            self.send(message)


class RedisStreamConsumer(BaseConsumer):
    def _to_message(self, data):
        if not isinstance(data, list):
            raise TypeError("data must be list")
        for row in data:
            _, message = row
            message = {
                k.decode() if isinstance(k, bytes) else k: v.decode() if isinstance(v, bytes) else v
                for k, v in message.items()
            }
            if '_message' in message:
                message = json.loads(message['_message'])
                yield Message(body=message.get("body"), extra=message.get("extra"), msg=row)
            else:
                yield Message(body=message, msg=row)
