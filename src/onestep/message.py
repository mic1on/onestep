import json


class Message:

    def __init__(self, message: dict, broker=None):
        """
        :param message: 接收到的消息体
        :param broker: 接收的broker
        """
        self.extra = message.pop("extra", {})
        self.body = message.copy()

        self.broker = broker
        self._exception = None

    def set_exception(self, exception):
        self.exception = exception

    @property
    def exception(self):
        return self._exception

    @exception.setter
    def exception(self, value):
        self.extra['failure_count'] = self.extra.get('failure_count', 0) + 1
        self._exception = value

    @property
    def fail(self):
        return self.exception is not None

    @property
    def failure_count(self):
        return self.extra.get('failure_count', 0)

    @failure_count.setter
    def failure_count(self, value):
        self.extra['failure_count'] = value

    def replace(self, **kwargs):
        """替换当前message的属性"""
        for key, value in kwargs.items():
            if not hasattr(self, key):
                continue
            setattr(self, key, value)
        return self

    def json(self):
        return json.dumps(self.dict())

    def dict(self):
        return {**self.body, 'extra': {**self.extra}}

    def ack(self):
        """确认消息"""
        if self.broker and hasattr(self.broker, "ack"):
            self.broker.ack(self)

    def nack(self, requeue=False):
        """拒绝消息"""
        if self.broker and hasattr(self.broker, "nack"):
            self.broker.nack(self, requeue)

    def __repr__(self):
        return f"<{self.__class__.__name__} {self.dict()}>"
