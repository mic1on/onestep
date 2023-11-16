import json
import sys
import time
import uuid
from dataclasses import dataclass
from traceback import TracebackException
from typing import Optional, Any, Union

from onestep._utils import catch_error


class MessageTracebackException(TracebackException):
    def __init__(self, exc_type, exc_value, exc_traceback, **kwargs):
        super().__init__(exc_type, exc_value, exc_traceback, **kwargs)
        self.exc_value = exc_value


@dataclass
class Extra:
    task_id: str = None
    publish_time: float = None
    failure_count: int = 0

    def __post_init__(self):
        self.task_id = self.task_id or str(uuid.uuid4())
        self.publish_time = self.publish_time or round(time.time(), 3)

    def to_dict(self):
        return {
            'task_id': self.task_id,
            'publish_time': self.publish_time,
            'failure_count': self.failure_count,
        }

    def __str__(self):
        return str(self.to_dict())


class Message:

    def __init__(
            self,
            body: Optional[Union[dict, Any]] = None,
            extra: Optional[Union[dict, Extra]] = None,
            msg: Optional[Any] = None,
            broker=None
    ):
        self.body = body
        self.extra = self._set_extra(extra)
        self.msg = msg

        self.broker = broker
        self._exception = None

    @staticmethod
    def _set_extra(extra):
        if isinstance(extra, Extra):
            return extra
        elif isinstance(extra, dict):
            return Extra(**extra)
        else:
            return Extra()

    def set_exception(self):
        """设置异常信息，会自动获取"""
        self._exception = MessageTracebackException(*sys.exc_info())
        self.failure_count = self.failure_count + 1

    @property
    def exception(self) -> Optional[MessageTracebackException]:
        return self._exception

    @property
    def fail(self):
        return self.exception is not None

    @property
    def failure_count(self):
        return self.extra.failure_count

    @failure_count.setter
    def failure_count(self, value):
        self.extra.failure_count = value

    def replace(self, **kwargs):
        """替换当前message的属性"""
        for key, value in kwargs.items():
            if not hasattr(self, key):
                continue
            if key == 'extra':
                value = self._set_extra(value)
            setattr(self, key, value)
        return self

    def to_dict(self, include_exception=False) -> dict:
        data = {'body': self.body, 'extra': self.extra.to_dict()}
        if include_exception and self.exception:
            data['exception'] = "".join(self.exception.format(chain=True))  # noqa

        return data

    def to_json(self, include_exception=False) -> str:
        return json.dumps(self.to_dict(include_exception))

    @catch_error()
    def confirm(self):
        """确认消息"""
        self.broker.confirm(self)

    @catch_error()
    def reject(self):
        """拒绝消息"""
        self.broker.reject(self)

    @catch_error()
    def requeue(self, is_source=False):
        """
        重发消息：先拒绝 再 重入
        
        :param is_source: 是否是源消息，True: 使用消息的最新数据重入当前队列，False: 使用消息的最新数据重入当前队列
        """
        self.broker.requeue(self, is_source=is_source)

    def __getattr__(self, item):
        return None

    def __delattr__(self, item):
        if hasattr(self, item):
            setattr(self, item, None)

    def __str__(self):
        return str(self.to_dict())

    def __repr__(self):
        return f"<{self.__class__.__name__} {self.body}>"


if __name__ == '__main__':
    msg = Message()
    msg.x = 1
    print(msg.x)
    msg.qq1 = 1
    del msg.qq1
    print(msg.qq1)
