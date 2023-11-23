class StopMiddleware(Exception):
    ...


class RetryException(Exception):
    def __init__(self, message=None, times=None, **kwargs):
        self.message = message
        self.times = times
        self.kwargs = kwargs


class RetryInQueue(RetryException):
    """消息重试-通过重试队列

    抛出此异常，消息将被重新放入队列，等待下次消费。
    """


class RetryInLocal(RetryException):
    """消息重试-本地

    不经过队列，直接在本地重试，直到达到重试次数。
    """


class DropMessage(Exception):
    """从 Brokers 中 丢弃该消息"""
