import hashlib
import json

from . import BaseMiddleware
from ..exception import DropMessage


class hash_func:
    """ Default message to hash function """

    def __call__(self, body):
        data = json.dumps(body) if isinstance(body, dict) else str(body)
        return hashlib.sha1(data.encode("utf-8")).hexdigest()


class UniqueMiddleware(BaseMiddleware):
    default_hash_func = hash_func()

    def before_consume(self, step, message, *args, **kwargs):
        message_hash = self.default_hash_func(message.body)
        if self.has_seen(message_hash):
            raise DropMessage(f"Message<{message}> has been seen before")

    def after_consume(self, step, message, *args, **kwargs):
        if message.fail or message.body is None:
            return
        message_hash = self.default_hash_func(message.body)
        self.mark_seen(message_hash)

    def has_seen(self, message):
        raise NotImplementedError

    def mark_seen(self, message):
        raise NotImplementedError


class MemoryUniqueMiddleware(UniqueMiddleware):
    """基于内存的去重中间件"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.seen = set()

    def has_seen(self, hash_value):
        return hash_value in self.seen

    def mark_seen(self, hash_value):
        self.seen.add(hash_value)
