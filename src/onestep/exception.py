class StopMiddleware(Exception):
    ...


class RetryException(Exception):
    def __init__(self, message=None, times=None, **kwargs):
        self.message = message
        self.times = times
        self.kwargs = kwargs


class RetryViaQueue(RetryException):
    """消息重试-通过重试队列"""

    def __init__(self, message=None, times=None, failure_queue=None, **kwargs):
        super().__init__(message=message, times=times, failure_queue=failure_queue, **kwargs)


class RetryViaLocal(RetryException):
    """消息重试-本地"""
    ...
