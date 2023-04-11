import json
import time
import uuid
from typing import Optional, Any, Union


class Extra:
    def __init__(self, task_id=None, publish_time=None, failure_count=0):
        self.task_id = task_id or str(uuid.uuid4())
        self.publish_time = publish_time or round(time.time(), 3)
        self.failure_count = failure_count
    
    def to_dict(self):
        return {
            'task_id': self.task_id,
            'publish_time': self.publish_time,
            'failure_count': self.failure_count,
        }
    
    def __str__(self):
        return str(self.to_dict())
    
    def __repr__(self):
        return f"{self.__class__.__name__}({self.task_id}, {self.publish_time}, {self.failure_count})"


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
    
    def set_exception(self, exception):
        self.exception = exception
        self.failure_count = self.failure_count + 1
    
    @property
    def exception(self):
        return self._exception
    
    @exception.setter
    def exception(self, value):
        self._exception = value
    
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
            # 保存 异常信息，包括 错误的类名 方法名 行号
            data['exception'] = {
                'type': self.exception.__class__.__name__,
                'message': str(self.exception),
            }
            if hasattr(self.exception, '__traceback__'):
                data['exception']['traceback'] = f"{self.exception.__traceback__.tb_frame.f_code.co_filename}:" \
                                                 f"{self.exception.__traceback__.tb_frame.f_code.co_name}:" \
                                                 f"{self.exception.__traceback__.tb_lineno}"
        
        return data
    
    def to_json(self, include_exception=False) -> str:
        return json.dumps(self.to_dict(include_exception))
    
    def confirm(self):
        """确认消息"""
        if self.broker:
            self.broker.confirm(self)
    
    def reject(self):
        """拒绝消息 （原始状态重入）"""
        if self.broker:
            self.broker.reject(self)
    
    def requeue(self):
        """重发消息 （最新状态重入）"""
        if self.broker:
            self.broker.requeue(self)
    
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
