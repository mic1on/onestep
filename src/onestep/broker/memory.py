import logging
import json
from queue import Queue, Full as FullException
from typing import Any, Optional

from .base import BaseBroker, BaseConsumer
from ..constants import DEFAULT_MEMORY_BROKER_MAXSIZE, DEFAULT_MEMORY_BROKER_TIMEOUT
from ..message import Message

logger = logging.getLogger(__name__)


class MemoryMessage(Message):

    @classmethod
    def from_broker(cls, broker_message: Any):
        if isinstance(broker_message, (str, bytes, bytearray)):
            try:
                message = json.loads(broker_message)
            except json.JSONDecodeError:
                message = {"body": broker_message}
        else:
            message = broker_message
        if not isinstance(message, dict):
            message = {"body": message}
        if "body" not in message:
            # 来自 外部的消息 可能没有 body, 故直接认为都是 message.body
            message = {"body": message}
        extra_val = message.get("extra")
        if not isinstance(extra_val, dict):
            extra_val = None
        return cls(body=message.get("body"), extra=extra_val, message=broker_message)


class MemoryBroker(BaseBroker):
    """内存消息队列 Broker

    使用 Python Queue 实现的内存消息队列，适用于测试和开发环境。

    特点：
    - 轻量级，无外部依赖
    - 进程内通信，速度快
    - 支持队列大小限制（防止内存溢出）
    - 非持久化，重启后消息丢失

    使用场景：
    - 单元测试
    - 开发调试
    - 简单的进程内消息传递

    不适用于：
    - 生产环境（消息不持久化）
    - 跨进程通信
    - 高可靠性要求场景

    使用示例：
    ```python
    broker = MemoryBroker(maxsize=1000)

    @step(from_broker=broker)
    def handler(message):
        print(message.body)

    step.start(block=True)
    ```
    """

    message_cls = MemoryMessage

    def __init__(self, maxsize: int = DEFAULT_MEMORY_BROKER_MAXSIZE, *args, **kwargs):
        """
        初始化内存 Broker

        :param maxsize: 队列最大大小（默认: 1000，防止内存溢出）
                     使用 0 表示无限制（不建议生产环境）
        :param args: 传递给 BaseBroker 的额外位置参数
        :param kwargs: 传递给 BaseBroker 的额外关键字参数
        """
        super().__init__(*args, **kwargs)
        self.queue: Queue = Queue(maxsize)

    def publish(self, message: Any, *args, **kwargs):
        """
        发布消息到内存队列

        使用非阻塞方式将消息放入队列。如果队列已满，消息会被丢弃并记录警告。

        :param message: 要发布的消息，可以是任意类型
        :param args: 额外的位置参数（未使用）
        :param kwargs: 额外的关键字参数（未使用）
        :raises: 不抛出异常（队列满时仅记录警告）
        """
        try:
            if self.queue is not None:
                self.queue.put_nowait(message)
        except FullException:
            logger.warning(
                f"队列已满，消息被丢弃。建议增加 maxsize 参数 (当前: {self.queue.maxsize})"
            )
            # 不抛出异常，避免中断执行流程

    def consume(self, *args, **kwargs):
        """
        创建消息消费者

        返回一个可迭代的消费者对象，支持超时获取消息。

        :param args: 额外的位置参数（未使用）
        :param kwargs: 额外的关键字参数
                      - timeout: 获取消息的超时时间（毫秒，默认: 5000）
        :return: BaseConsumer 实例，支持迭代获取消息
        """

    def confirm(self, message: Message):
        """确认消息已成功处理

        内存队列不需要手动确认，此方法为空操作。

        :param message: 要确认的消息对象
        """
        pass

    def reject(self, message: Message):
        """拒绝消息

        内存队列不支持拒绝机制，此方法为空操作。

        :param message: 要拒绝的消息对象
        """
        pass

    def requeue(self, message: Message, is_source=False):
        """
        重新排队消息

        :param message: 要重新排队的消息对象
        :param is_source: 如果为 True，使用消息的原始内容重新入队
                         如果为 False，使用消息的当前状态重新入队
        """
        if is_source:
            self.publish(message.message)
        else:
            self.send(message)

    def __repr__(self):
        return f"<{self.__class__.__name__} {self.name}>"

    def __str__(self):
        return self.name


class MemoryConsumer(BaseConsumer):
    ...
