from abc import ABC, abstractmethod
from typing import Optional, Tuple
from .exception import RetryViaLocal, RetryViaQueue
from .message import Message


class BaseRetry(ABC):

    @abstractmethod
    def __call__(self, *args, **kwargs) -> Optional[bool]:
        """True=继续（执行重试）， False=结束（执行回调），None=结束（忽略回调）"""
        pass


class NeverRetry(BaseRetry):

    def __call__(self, message) -> Optional[bool]:
        return False


class AlwaysRetry(BaseRetry):

    def __call__(self, message) -> Optional[bool]:
        return True


class TimesRetry(BaseRetry):

    def __init__(self, times: int = 3):
        self.times = times

    def __call__(self, message) -> Optional[bool]:
        return message.failure_count < self.times


class RetryIfException(BaseRetry):

    def __init__(self, exceptions: Optional[Tuple[Exception]] = Exception):
        self.exceptions = exceptions

    def __call__(self, message) -> Optional[bool]:
        return isinstance(message.exception, self.exceptions)  # noqa


class LocalAndQueueRetry(TimesRetry):

    def __call__(self, message: Message) -> Optional[bool]:
        """本地重试或队列重试"""
        if isinstance(message.exception, (RetryViaLocal, RetryViaQueue)):
            if message.failure_count < (message.exception.times or self.times):
                if isinstance(message.exception, RetryViaQueue):
                    return None  # 入队重试，不回调
                return True  # 本地重试，不回调
            else:
                return False  # 不重试了，回调
        else:
            return False  # 其他异常，回调


# error callback
class BaseErrorCallback(ABC):

    @abstractmethod
    def __call__(self, *args, **kwargs):
        pass


class NackErrorCallBack(BaseErrorCallback):

    def __call__(self, message):
        message.reject()
