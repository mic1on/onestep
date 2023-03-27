# -*- coding: utf-8 -*-
from ..exception import StopMiddleware


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
        """
        如果当前Broker是Job的to_broker, 则必须实现此方法
        """
        raise NotImplementedError('Please implement in subclasses.')

    def consume(self, *args, **kwargs):
        """
        如果当前Broker是Job的from_broker, 则必须实现此方法
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
