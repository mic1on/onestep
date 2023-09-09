from abc import ABC, abstractmethod
from typing import Optional, Tuple, Union, Type

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
    
    def __init__(self, exceptions: Optional[Tuple[Union[Exception, Type]]] = Exception):
        self.exceptions = exceptions
    
    def __call__(self, message) -> Optional[bool]:
        return isinstance(message.exception.exc_value, self.exceptions)  # noqa


class AdvancedRetry(TimesRetry):
    """高级重试策略
    
    1. 本地重试：如果异常是 RetryViaLocal 或 指定异常，且重试次数未达到上限，则本地重试，不回调
    2. 队列重试：如果异常是 RetryViaQueue，且重试次数未达到上限，则入队重试，不回调
    3. 其他异常：如果异常不是 RetryViaLocal 或 RetryViaQueue 或 指定异常，则不重试，回调
    注：待重试的异常若继承自 RetryException，则可单独指定重试次数，否则默认为 3 次
    """
    def __init__(self, times: int = 3, exceptions: Optional[Tuple[Union[Exception, Type]]] = None):
        super().__init__(times=times)
        self.exceptions = (RetryViaLocal, RetryViaQueue) + (exceptions or ())
    
    def __call__(self, message: Message) -> Optional[bool]:
        if isinstance(message.exception.exc_value, self.exceptions):
            max_retry_times = getattr(message.exception.exc_value, "times", None) or self.times
            if message.failure_count < max_retry_times:
                if isinstance(message.exception.exc_value, RetryViaQueue):
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
