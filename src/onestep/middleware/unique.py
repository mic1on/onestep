import hashlib
import json
from typing import Callable, Any

from .base import BaseMiddleware
from ..exception import DropMessage


class hash_func:
    """ Default message to hash function """

    def __call__(self, body: Any) -> str:
        data = json.dumps(body) if isinstance(body, dict) else str(body)
        return hashlib.sha1(data.encode("utf-8")).hexdigest()


class UniqueMiddleware(BaseMiddleware):
    """去重中间件基类

    防止重复消息被处理。通过哈希值判断消息是否已经处理过。

    使用示例：
    ```python
    from onestep.middleware import MemoryUniqueMiddleware

    @step(from_broker=broker, middlewares=[MemoryUniqueMiddleware()])
    def handler(message):
        return message.body
    ```
    """

    default_hash_func: Callable[[Any], str] = hash_func()

    def before_consume(self, step: Any, message: Any, *args: Any, **kwargs: Any) -> None:
        """消费消息之前，检查是否已处理过"""
        message_hash = self.default_hash_func(message.body)
        if self.has_seen(message_hash):
            raise DropMessage(f"Message<{message}> has been seen before")

    def after_consume(self, step: Any, message: Any, *args: Any, **kwargs: Any) -> None:
        """消费消息之后，标记为已处理"""
        if message.fail or message.body is None:
            return
        message_hash = self.default_hash_func(message.body)
        self.mark_seen(message_hash)

    def has_seen(self, hash_value: str) -> bool:
        """检查消息哈希是否已存在

        :param hash_value: 消息哈希值
        :return: 是否已存在
        """
        raise NotImplementedError

    def mark_seen(self, hash_value: str) -> None:
        """标记消息哈希为已处理

        :param hash_value: 消息哈希值
        """
        raise NotImplementedError


class MemoryUniqueMiddleware(UniqueMiddleware):
    """基于内存的去重中间件

    使用 Python 的 set 来存储已处理消息的哈希值。
    注意：重启后数据会丢失。

    使用示例：
    ```python
    @step(from_broker=broker, middlewares=[MemoryUniqueMiddleware()])
    def handler(message):
        return message.body
    ```
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.seen: set[str] = set()

    def has_seen(self, hash_value: str) -> bool:
        """检查消息哈希是否已存在"""
        return hash_value in self.seen

    def mark_seen(self, hash_value: str) -> None:
        """标记消息哈希为已处理"""
        self.seen.add(hash_value)
