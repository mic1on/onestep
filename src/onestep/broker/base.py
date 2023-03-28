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
        if message.extra.get('task_id') is None:
            message.extra['task_id'] = str(uuid.uuid4())
        if message.extra.get('publish_time') is None:
            message.extra['publish_time'] = round(time.time(), 3)
        # TODO: 对消息发送进行N次重试，确保消息发送成功。
        return self.publish(json.dumps(message.to_dict()))

    def publish(self, message):
        """
        如果当前Broker是Job的to_broker, 则必须实现此方法
        """
        raise NotImplementedError('Please implement in subclasses.')

    def consume(self, *args, **kwargs):
        """
        如果当前Broker是Job的from_broker, 则必须实现此方法
        """
        raise NotImplementedError('Please implement in subclasses.')

    def requeue(self, message):
        """重新入队，与 nack 不同的是：nack(requeue=True) 是将消息按【原样】重入，本方法是将消息的【最新状态】重发 """
        self.send(message)

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
