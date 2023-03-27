from abc import ABC, abstractmethod
from typing import Optional, Tuple
from .exception import RetryViaLocal, RetryViaQueue


class BaseRetry(ABC):

    @abstractmethod
    def __call__(self, *args, **kwargs) -> Optional[bool]:
        """True=继续（执行重试）， False=结束（执行回调），None=结束（直接跳过）"""
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


class MyRetry(TimesRetry):

    def __call__(self, message) -> Optional[bool]:
        """本地重试或队列重试"""
        if isinstance(message.exception, (RetryViaLocal, RetryViaQueue)):
            if message.failure_count < (message.exception.times or self.times):
                if isinstance(message.exception, RetryViaQueue):
                    message.nack(requeue=True)
                    return None  # 入队重试，不回调
                return True
            else:
                return False  # 不重试了，回调


# error callback
class BaseErrorCallback(ABC):

    @abstractmethod
    def __call__(self, *args, **kwargs):
        pass


class NackErrorCallBack(BaseErrorCallback):

    def __call__(self, message):
        message.nack()
