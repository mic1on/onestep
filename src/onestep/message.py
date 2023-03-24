from copy import deepcopy


class Message:

    def __init__(self, message, broker=None):
        """
        :param message: 接收到的消息体
        :param broker: 接收的broker
        """
        self.message = message
        self.broker = broker
        self._exception = None
        self._fail_number = 0

    def set_exception(self, exception):
        self.exception = exception

    @property
    def exception(self):
        return self._exception

    @exception.setter
    def exception(self, value):
        self.fail_number += 1
        self._exception = value

    @property
    def fail(self):
        return self.exception is not None

    @property
    def fail_number(self):
        return self._fail_number

    @fail_number.setter
    def fail_number(self, value):
        self._fail_number = value

    def replace(self, **kwargs):
        """替换当前message的属性"""
        for key, value in kwargs.items():
            if not hasattr(self, key):
                continue
            setattr(self, key, value)
        return self

    def ack(self):
        """确认消息"""
        if self.broker and hasattr(self.broker, "ack"):
            self.broker.ack(self)

    def nack(self):
        """拒绝消息"""
        if self.broker and hasattr(self.broker, "nack"):
            self.broker.nack(self)

    def __repr__(self):
        return f"<{self.__class__.__name__} {self.message}>"
