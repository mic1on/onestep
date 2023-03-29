# -*- coding: utf-8 -*-
import abc
import json
import time
import uuid
from queue import Queue, Empty

from ..exception import StopMiddleware
from ..message import Message


class BaseBroker:

    def __init__(self, name=None, queue=None, middlewares=None):
        self.queue = queue
        self.name = name or "broker"
        self.middlewares = []

        if middlewares:
            for middleware in middlewares:
                self.add_middleware(middleware)

    def add_middleware(self, middleware):
        self.middlewares.append(middleware)

    def send(self, message):
        """对消息进行预处理，然后再发送"""
        if not isinstance(message, Message):
            message = Message(body=message)
        message.init_extra()
        # TODO: 对消息发送进行N次重试，确保消息发送成功。
        return self.publish(message.to_json())

    @abc.abstractmethod
    def publish(self, message):
        """
        如果当前Broker是Job的to_broker, 则必须实现此方法
        """
        raise NotImplementedError('Please implement in subclasses.')

    @abc.abstractmethod
    def consume(self, *args, **kwargs):
        """
        如果当前Broker是Job的from_broker, 则必须实现此方法
        """
        raise NotImplementedError('Please implement in subclasses.')

    @abc.abstractmethod
    def confirm(self, message):
        """确认消息"""
        raise NotImplementedError('Please implement in subclasses.')

    @abc.abstractmethod
    def reject(self, message):
        """拒绝消息"""
        raise NotImplementedError('Please implement in subclasses.')

    @abc.abstractmethod
    def requeue(self, message, is_source=False):
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

    def __repr__(self):
        return f"<{self.__class__.__name__} {self.name}>"

    def __str__(self):
        return self.name


class BaseConsumer:

    def __init__(self, queue: Queue, *args, **kwargs):
        self.queue = queue
        self.timeout = kwargs.pop("timeout", 1000)

    @abc.abstractmethod
    def _to_message(self, data):
        """
        转换消息内容到 Message , 则必须实现此方法
        """
        raise NotImplementedError('Please implement in subclasses.')

    def __next__(self):
        try:
            return self._to_message(self.queue.get(timeout=self.timeout / 1000))
        except Empty:
            return None

    def __iter__(self):
        return self
