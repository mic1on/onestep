import json
import logging
import os
import sys
import time
import uuid
from dataclasses import dataclass
from traceback import TracebackException
from typing import Optional, Any, Union, Type, Dict, List
from types import TracebackType

from onestep._utils import catch_error

logger = logging.getLogger(__name__)


class MessageTracebackException(TracebackException):
    def __init__(self, exc_type: Type[BaseException], exc_value: BaseException, exc_traceback: Optional[TracebackType], **kwargs):
        super().__init__(exc_type, exc_value, exc_traceback, **kwargs)
        self.exc_value = exc_value


@dataclass
class Extra:
    task_id: Optional[str] = None
    publish_time: Optional[float] = None
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
            message: Optional[Any] = None,
            broker=None
    ):
        """ Message

        :param body: 解析后的消息体
        :param extra: 额外信息
        :param message: 原始消息体
        :param broker: 当前消息所属的 broker
        """
        self.body = body
        self.extra = self._set_extra(extra)
        self.message = message

        self.broker = broker
        self._exception: Optional[MessageTracebackException] = None

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
        exc_type, exc_value, exc_tb = sys.exc_info()
        if exc_type is None or exc_value is None:
            exc_type = Exception
            exc_value = Exception("No exception info")
        self._exception = MessageTracebackException(exc_type, exc_value, exc_tb)
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

    def to_dict(self, include_exception=False, redact_sensitive=False, sensitive_keys: Optional[List[str]] = None) -> dict:
        """
        转换为字典

        :param include_exception: 是否包含异常信息
        :param redact_sensitive: 是否脱敏敏感数据
        :param sensitive_keys: 需要脱敏的键列表（如果为 None，使用默认列表）
        :return: 消息字典
        """
        body = self.body

        # 如果需要脱敏
        if redact_sensitive:
            body = self._redact_sensitive_data(body, sensitive_keys)

        data = {'body': body, 'extra': self.extra.to_dict()}

        if include_exception and self.exception:
            data['exception'] = "".join(self.exception.format(chain=True))  # noqa

        return data

    @staticmethod
    def _redact_sensitive_data(data: Any, sensitive_keys: Optional[List[str]] = None) -> Any:
        """
        脱敏敏感数据

        :param data: 要脱敏的数据
        :param sensitive_keys: 需要脱敏的键列表
        :return: 脱敏后的数据
        """
        # 默认的敏感键列表
        default_sensitive_keys = [
            'password', 'passwd', 'pwd',
            'secret', 'token', 'apikey', 'api_key',
            'authorization', 'auth',
            'credit_card', 'card_number',
            'ssn', 'social_security_number',
            'phone', 'mobile', 'telephone',
            'email', 'email_address',
        ]

        keys_to_redact = sensitive_keys or default_sensitive_keys

        # 处理字典
        if isinstance(data, dict):
            return {
                k: '***REDACTED***' if Message._is_sensitive_key(k, keys_to_redact)
                else Message._redact_sensitive_data(v, keys_to_redact)
                for k, v in data.items()
            }

        # 处理列表
        elif isinstance(data, (list, tuple)):
            return [Message._redact_sensitive_data(item, keys_to_redact) for item in data]

        # 其他类型直接返回
        else:
            return data

    @staticmethod
    def _is_sensitive_key(key: str, sensitive_keys: List[str]) -> bool:
        """
        检查键是否敏感

        :param key: 键名
        :param sensitive_keys: 敏感键列表
        :return: 是否敏感
        """
        key_lower = key.lower()
        for sensitive in sensitive_keys:
            # 完全匹配或包含敏感词
            if sensitive in key_lower:
                return True
        return False

    def to_json(self, include_exception=False) -> str:
        return json.dumps(self.to_dict(include_exception))

    @catch_error()
    def confirm(self, **kwargs):
        """确认消息"""
        broker = getattr(self, 'broker', None)
        if broker and hasattr(broker, 'confirm'):
            if hasattr(broker, 'before_emit'):
                broker.before_emit('confirm', message=self, step=kwargs.get('step'))
            broker.confirm(self)
            if hasattr(broker, 'after_emit'):
                broker.after_emit('confirm', message=self, step=kwargs.get('step'))

    @catch_error()
    def reject(self, **kwargs):
        """拒绝消息"""
        broker = getattr(self, 'broker', None)
        if broker and hasattr(broker, 'reject'):
            if hasattr(broker, 'before_emit'):
                broker.before_emit('reject', message=self, step=kwargs.get('step'))
            broker.reject(self)
            if hasattr(broker, 'after_emit'):
                broker.after_emit('reject', message=self, step=kwargs.get('step'))

    @catch_error()
    def requeue(self, is_source=False, **kwargs):
        """
        重发消息：先拒绝 再 重入
        
        :param is_source: 是否是源消息，True: 使用消息的最新数据重入当前队列，False: 使用消息的最新数据重入当前队列
        """
        broker = getattr(self, 'broker', None)
        if broker and hasattr(broker, 'requeue'):
            if hasattr(broker, 'before_emit'):
                broker.before_emit('requeue', message=self, step=kwargs.get('step'))
            broker.requeue(self, is_source=is_source)
            if hasattr(broker, 'after_emit'):
                broker.after_emit('requeue', message=self, step=kwargs.get('step'))

    def __getattr__(self, item):
        return None

    def __delattr__(self, item):
        if hasattr(self, item):
            setattr(self, item, None)

    def __str__(self):
        """
        返回字符串表示（默认启用脱敏）

        可以通过环境变量 ONESTEP_LOG_REDACT=true/false 控制脱敏
        """
        # 默认启用脱敏（生产环境更安全）
        enable_redaction = os.getenv('ONESTEP_LOG_REDACT', 'true').lower() in ('true', '1', 'yes')

        return str(self.to_dict(redact_sensitive=enable_redaction))

    def __repr__(self):
        return f"<{self.__class__.__name__} {self.body}>"

    @classmethod
    def from_broker(cls, broker_message: Any):
        return cls(body=broker_message, extra=None, message=broker_message, broker=None)
