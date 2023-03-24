from abc import ABC, abstractmethod
from typing import Optional, Tuple


class BaseRetry(ABC):

    @abstractmethod
    def __call__(self, *args, **kwargs) -> bool:
        pass


class NeverRetry(BaseRetry):

    def __call__(self, message) -> bool:
        return False


class AlwaysRetry(BaseRetry):

    def __call__(self, message) -> bool:
        return True


class TimesRetry(BaseRetry):

    def __init__(self, times: int = 3):
        self.times = times

    def __call__(self, message) -> bool:
        return message.fail_number < self.times


class RetryIfException(BaseRetry):

    def __init__(self, exceptions: Optional[Tuple[Exception]] = Exception):
        self.exceptions = exceptions

    def __call__(self, message) -> bool:
        return isinstance(message.exception, self.exceptions)


# error callback

class BaseErrorCallback(ABC):

    @abstractmethod
    def __call__(self, *args, **kwargs):
        pass


class NackErrorCallBack(BaseErrorCallback):

    def __call__(self, message):
        message.nack()
