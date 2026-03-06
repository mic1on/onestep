# -*- coding: utf-8 -*-
import abc
import logging
import time
from queue import Queue, Empty
from typing import Any, Optional, List, Callable, Type

from onestep.middleware import BaseMiddleware
from onestep.exception import StopMiddleware
from onestep.message import Message
from onestep.constants import DEFAULT_MEMORY_BROKER_TIMEOUT, MILLISECONDS_PER_SECOND

logger = logging.getLogger(__name__)


class BaseBroker:
    """Broker 基类

    定义消息队列的标准接口，所有 Broker 必须继承此类并实现抽象方法。

    功能：
    - 消息发送和接收
    - 中间件支持
    - 消息确认和重试机制
    - 自动重试（支持指数退避）

    使用示例：
    ```python
    broker = CustomBroker(name="my_broker")
    broker.publish({"data": "value"})
    consumer = broker.consume()
    for message in consumer:
        print(message.body)
    ```
    """
    message_cls: Type[Message] = Message

    def __init__(self,
                 name: Optional[str] = None,
                 queue: Optional[Queue] = None,
                 middlewares: Optional[List[BaseMiddleware]] = None,
                 once: bool = False,
                 cancel_consume: Optional[Callable] = None,
                 send_retry_times: int = 3,
                 send_retry_delay: float = 1.0):
        """
        初始化 Broker

        :param name: Broker 名称（用于标识和日志）
        :param queue: 队列对象（可选，用于内存队列等实现）
        :param middlewares: 中间件列表，用于消息处理前后的拦截和增强
        :param once: 是否只运行一次（接收一条消息后自动关闭）
        :param cancel_consume: 取消消费的回调函数
                             接收 message 参数，返回 bool 表示是否关闭
        :param send_retry_times: 消息发送失败时的重试次数（默认: 3）
        :param send_retry_delay: 消息发送重试的初始延迟（秒，默认: 1.0）
                                重试时会使用指数退避策略
        """
        self.queue = queue
        self.name = name or "broker"
        self.middlewares: List[BaseMiddleware] = []
        self.once = once
        self.cancel_consume = cancel_consume
        self.send_retry_times = send_retry_times
        self.send_retry_delay = send_retry_delay

        if middlewares:
            for middleware in middlewares:
                self.add_middleware(middleware)

    def add_middleware(self, middleware: BaseMiddleware):
        """添加中间件

        :param middleware: 中间件实例，必须继承自 BaseMiddleware
        :raises TypeError: 如果 middleware 不是 BaseMiddleware 实例
        """
        if not isinstance(middleware, BaseMiddleware):
            raise TypeError(f"middleware must be BaseMiddleware instance, not {type(middleware)}")
        self.middlewares.append(middleware)

    def send(self, message, *args, **kwargs):
        """发送消息到 Broker

        对消息进行预处理（中间件），然后发送到目标队列。
        如果发送失败，会自动重试（使用指数退避策略）。

        :param message: 要发送的消息，可以是任意类型或 Message 对象
        :param args: 额外的位置参数，传递给 publish 方法
        :param kwargs: 额外的关键字参数，可包含 step 参数
        :return: 发送结果（由 publish 方法返回）
        :raises Exception: 重试次数用尽后仍然失败时抛出
        """
        step = kwargs.pop("step", None)
        if not isinstance(message, Message):
            message = self.message_cls(body=message)
        self.before_emit("send", message=message, step=step)

        # 消息发送重试机制
        result = None
        for attempt in range(self.send_retry_times):
            try:
                result = self.publish(message.to_json(), *args, **kwargs)
                break  # 发送成功，退出重试
            except Exception as e:
                if attempt < self.send_retry_times - 1:
                    # 使用指数退避策略
                    delay = self.send_retry_delay * (2 ** attempt)
                    logger.warning(
                        f"Message send failed (attempt {attempt + 1}/{self.send_retry_times}), "
                        f"retrying in {delay}s: {e}"
                    )
                    time.sleep(delay)
                else:
                    logger.error(
                        f"Message send failed after {self.send_retry_times} attempts: {e}"
                    )
                    raise

        self.after_emit("send", message=message, step=step)
        return result

    @abc.abstractmethod
    def publish(self, message: Any, *args, **kwargs):
        """
        发布消息到 Broker

        子类必须实现此方法，将消息发送到目标队列或存储。

        :param message: 要发布的消息内容（通常是 JSON 字符串）
        :param args: 额外的位置参数
        :param kwargs: 额外的关键字参数
        :raises NotImplementedError: 子类未实现此方法时抛出
        """
        raise NotImplementedError('Please implement in subclasses.')

    @abc.abstractmethod
    def consume(self, *args, **kwargs):
        """
        创建消息消费者

        子类必须实现此方法，返回一个消费者对象。
        消费者应该是可迭代的，每次迭代返回一条消息。

        :param args: 额外的位置参数
        :param kwargs: 额外的关键字参数（如 timeout）
        :return: 消费者对象（可迭代）
        :raises NotImplementedError: 子类未实现此方法时抛出
        """
        raise NotImplementedError('Please implement in subclasses.')

    @abc.abstractmethod
    def confirm(self, message: Message):
        """确认消息已成功处理

        子类必须实现此方法，将消息标记为已完成。

        :param message: 要确认的消息对象
        :raises NotImplementedError: 子类未实现此方法时抛出
        """
        raise NotImplementedError('Please implement in subclasses.')

    @abc.abstractmethod
    def reject(self, message: Message):
        """拒绝消息

        子类必须实现此方法，将消息标记为被拒绝。
        消息可能被丢弃或进入死信队列（取决于实现）。

        :param message: 要拒绝的消息对象
        :raises NotImplementedError: 子类未实现此方法时抛出
        """
        raise NotImplementedError('Please implement in subclasses.')

    @abc.abstractmethod
    def requeue(self, message: Message, is_source=False):
        """
        重新排队消息

        子类必须实现此方法，将消息重新放回队列。

        :param message: 要重新排队的消息对象
        :param is_source: 如果为 True，使用消息的初始状态重新入队
                         如果为 False，使用消息的当前状态重新入队
        :raises NotImplementedError: 子类未实现此方法时抛出
        """
        raise NotImplementedError('Please implement in subclasses.')

    def before_emit(self, signal, **kwargs):
        signal = "before_" + signal
        self._emit(signal, **kwargs)

    def after_emit(self, signal, **kwargs):
        signal = "after_" + signal
        self._emit(signal, **kwargs)

    def _emit(self, signal, **kwargs):
        for middleware in self.middlewares:
            if not hasattr(middleware, signal):
                continue
            try:
                params = dict(kwargs)
                params.setdefault("broker", self)
                params.setdefault("step", None)
                getattr(middleware, signal)(**params)
            except StopMiddleware:
                break

    def shutdown(self):
        """关闭Broker"""

    def __repr__(self):
        return f"<{self.__class__.__name__} {self.name}>"

    def __str__(self):
        return self.name


class BaseConsumer:
    """消息消费者基类

    实现消息队列的基本消费逻辑，支持超时和迭代获取。

    使用示例：
    ```python
    consumer = broker.consume(timeout=5000)
    for message in consumer:
        if message is None:
            break  # 超时或队列为空
        print(message.body)
        broker.confirm(message)
    ```
    """

    def __init__(self, broker: BaseBroker, *args, **kwargs):
        """
        初始化消费者

        :param broker: Broker 实例
        :param args: 额外的位置参数
        :param kwargs: 额外的关键字参数
                       - timeout: 获取消息的超时时间（毫秒，默认: 5000）
        """
        self.queue = broker.queue
        self.message_cls: Type[Message] = broker.message_cls if broker.message_cls else Message
        # 使用默认超时时间（可通过 timeout 参数覆盖）
        self.timeout = kwargs.pop("timeout", DEFAULT_MEMORY_BROKER_TIMEOUT)

    def __next__(self):
        """
        从队列获取下一条消息

        支持超时机制：
        - timeout > 0：阻塞等待，超时返回 None
        - timeout <= 0：非阻塞获取，队列为空返回 None

        :return: Message 对象或 None（队列为空或超时）
        :raises ValueError: timeout 配置无效时抛出
        """
        try:
            q = self.queue
            if q is None:
                return None
            timeout_ms = self.timeout
            if isinstance(timeout_ms, (int, float)) and timeout_ms > 0:
                data = q.get(timeout=timeout_ms / MILLISECONDS_PER_SECOND)
            else:
                # 当超时为0、负数或非数字时，使用非阻塞获取以避免ValueError
                data = q.get_nowait()
            return self.message_cls.from_broker(broker_message=data)
        except Empty:
            # 队列为空，这是正常情况，不需要记录日志
            return None
        except ValueError as e:
            logger.warning(f"队列获取失败，无效的超时配置: timeout_ms={self.timeout}, error: {e}")
            return None

    def __iter__(self):
        """返回自身，支持迭代协议"""
        return self
