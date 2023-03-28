import json
import time
import uuid
from typing import Optional, Any, Union


class Message:

    def __init__(
            self,
            body: Optional[Union[dict, Any]] = None,
            extra: Optional[dict] = None,
            msg: Optional[Any] = None,
            broker=None
    ):
        self.body = body
        self.extra = extra or {}
        self.msg = msg

        self.broker = broker
        self._exception = None

    def init_extra(self):
        self.extra.setdefault("task_id", f"{uuid.uuid4()}")
        self.extra.setdefault("publish_time", round(time.time(), 3))

    def set_exception(self, exception):
        self.exception = exception
        self.failure_count = self.failure_count + 1

    @property
    def exception(self):
        return self._exception

    @exception.setter
    def exception(self, value):
        # self.extra['failure_count'] = self.extra.get('failure_count', 0) + 1
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

    def to_dict(self) -> dict:
        return {'body': self.body, 'extra': {**self.extra}}

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    def ack(self):
        """确认消息"""
        if self.broker and hasattr(self.broker, "ack"):
            self.broker.ack(self)

    def nack(self, requeue=False):
        """拒绝消息 （原始状态重入）"""
        if self.broker and hasattr(self.broker, "nack"):
            self.broker.nack(self, requeue)

    def requeue(self):
        """重发消息 （最新状态重入）"""
        if self.broker and hasattr(self.broker, "requeue"):
            self.broker.requeue(self)

    def __str__(self):
        return str(self.to_dict())

    def __repr__(self):
        return f"<{self.__class__.__name__} {self.body}>"
