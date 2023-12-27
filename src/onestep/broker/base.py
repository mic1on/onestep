# -*- coding: utf-8 -*-
import abc
import logging
from queue import Queue, Empty
from typing import Any, Optional, List, Callable

from onestep.middleware import BaseMiddleware
from onestep.exception import StopMiddleware
from onestep.message import Message

logger = logging.getLogger(__name__)


class BaseBroker:
    message_cls = Message

    def __init__(self,
                 name: Optional[str] = None,
                 queue: Optional[Queue] = None,
                 middlewares: Optional[List[BaseMiddleware]] = None,
                 once: bool = False,
                 cancel_consume: Optional[Callable] = None):
        """
        @param name: broker name
        @param queue: broker queue name
        @param middlewares: broker middlewares
        @param once: just run once
            if once is True, when broker receive a message, it will shutdown
        @param cancel_consume: cancel consume
            cancel_consume(message) -> bool
            if cancel_consume return True, broker will shutdown
        """
        self.queue = queue
        self.name = name or "broker"
        self.middlewares = []
        self.once = once
        self.cancel_consume = cancel_consume

        if middlewares:
            for middleware in middlewares:
                self.add_middleware(middleware)

    def add_middleware(self, middleware: BaseMiddleware):
        self.middlewares.append(middleware)

    def send(self, message):
        """对消息进行预处理，然后再发送"""
        if not isinstance(message, Message):
            message = self.message_cls(body=message)
        # TODO: 对消息发送进行N次重试，确保消息发送成功。
        return self.publish(message.to_json())

    @abc.abstractmethod
    def publish(self, message: Any):
        """
        将消息原样发布到 broker 中。如果当前Broker是Job的to_broker, 则必须实现此方法
        """
        raise NotImplementedError('Please implement in subclasses.')

    @abc.abstractmethod
    def consume(self, *args, **kwargs):
        """
        消费消息。如果当前Broker是Job的from_broker, 则必须实现此方法
        """
        raise NotImplementedError('Please implement in subclasses.')

    @abc.abstractmethod
    def confirm(self, message: Message):
        """确认消息"""
        raise NotImplementedError('Please implement in subclasses.')

    @abc.abstractmethod
    def reject(self, message: Message):
        """拒绝消息"""
        raise NotImplementedError('Please implement in subclasses.')

    @abc.abstractmethod
    def requeue(self, message: Message, is_source=False):
        """
        重发消息：先拒绝 再 重入
        is_source = False 重入使用消息的当前状态
        is_source = True 重入使用消息的初始状态
        """
        raise NotImplementedError('Please implement in subclasses.')

    def before_emit(self, signal, *args, **kwargs):
        signal = "before_" + signal
        self._emit(signal, *args, **kwargs)

    def after_emit(self, signal, *args, **kwargs):
        signal = "after_" + signal
        self._emit(signal, *args, **kwargs)

    def _emit(self, signal, *args, **kwargs):
        for middleware in self.middlewares:
            if not hasattr(middleware, signal):
                continue
            try:
                getattr(middleware, signal)(self, *args, **kwargs)
            except StopMiddleware:
                break

    def shutdown(self):
        """关闭Broker"""

    def __repr__(self):
        return f"<{self.__class__.__name__} {self.name}>"

    def __str__(self):
        return self.name


class BaseConsumer:

    def __init__(self, broker: BaseBroker, *args, **kwargs):
        self.queue = broker.queue
        self.message_cls = broker.message_cls or Message
        self.timeout = kwargs.pop("timeout", 1000)

    def __next__(self):
        try:
            data = self.queue.get(timeout=self.timeout / 1000)
            return self.message_cls.from_broker(broker_message=data)
        except Empty:
            return None

    def __iter__(self):
        return self
