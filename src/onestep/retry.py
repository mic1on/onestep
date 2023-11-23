from abc import ABC, abstractmethod
from enum import Enum
from typing import Optional, Tuple, Union, Type

from .exception import RetryInLocal, RetryInQueue
from .message import Message


class RetryStatus(Enum):
    CONTINUE = 1  # 继续（执行重试）
    END_WITH_CALLBACK = 2  # 结束（执行回调）
    END_IGNORE_CALLBACK = 3  # 结束（忽略回调）


class BaseRetry(ABC):

    @abstractmethod
    def __call__(self, *args, **kwargs) -> Optional[RetryStatus]:
        pass


class NeverRetry(BaseRetry):

    def __call__(self, message) -> Optional[RetryStatus]:
        return RetryStatus.END_WITH_CALLBACK


class AlwaysRetry(BaseRetry):

    def __call__(self, message) -> Optional[RetryStatus]:
        return RetryStatus.CONTINUE


class TimesRetry(BaseRetry):

    def __init__(self, times: int = 3):
        self.times = times

    def __call__(self, message) -> Optional[RetryStatus]:
        if message.failure_count < self.times:
            return RetryStatus.CONTINUE
        return RetryStatus.END_WITH_CALLBACK


class RetryIfException(BaseRetry):

    def __init__(self, exceptions: Optional[Tuple[Union[Exception, Type]]] = Exception):
        self.exceptions = exceptions

    def __call__(self, message) -> Optional[RetryStatus]:
        if isinstance(message.exception.exc_value, self.exceptions):
            return RetryStatus.CONTINUE
        return RetryStatus.END_WITH_CALLBACK


class AdvancedRetry(TimesRetry):
    """高级重试策略
    
    1. 本地重试：如果异常是 RetryInLocal 或 指定异常，且重试次数未达到上限，则本地重试，不回调
    2. 队列重试：如果异常是 RetryInQueue，且重试次数未达到上限，则入队重试，不回调
    3. 其他异常：如果异常不是 RetryInLocal 或 RetryInQueue 或 指定异常，则不重试，回调
    注：待重试的异常若继承自 RetryException，则可单独指定重试次数，否则默认为 3 次
    """

    def __init__(self, times: int = 3, exceptions: Optional[Tuple[Union[Exception, Type]]] = None):
        super().__init__(times=times)
        self.exceptions = (RetryInLocal, RetryInQueue) + (exceptions or ())

    def __call__(self, message: Message) -> Optional[RetryStatus]:
        if isinstance(message.exception.exc_value, self.exceptions):
            max_retry_times = getattr(message.exception.exc_value, "times", None) or self.times
            if message.failure_count < max_retry_times:
                if isinstance(message.exception.exc_value, RetryInQueue):
                    return RetryStatus.END_IGNORE_CALLBACK
                return RetryStatus.CONTINUE
            else:
                return RetryStatus.END_WITH_CALLBACK
        else:
            return RetryStatus.END_WITH_CALLBACK


# error callback
class BaseErrorCallback(ABC):

    @abstractmethod
    def __call__(self, *args, **kwargs):
        pass


class NackErrorCallBack(BaseErrorCallback):

    def __call__(self, message):
        message.reject()
