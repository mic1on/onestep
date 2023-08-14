import functools
import collections
import logging

from inspect import isgenerator, iscoroutinefunction, isasyncgenfunction, isasyncgen
from typing import Optional, List, Dict, Any, Callable, Union

from .broker.base import BaseBroker
from .exception import StopMiddleware
from .message import Message
from .retry import NeverRetry
from .signal import message_sent, started, stopped
from .state import State
from .worker import WorkerThread

logger = logging.getLogger(__name__)

DEFAULT_WORKERS = 1


class BaseOneStep:
    consumers: Dict[str, List[WorkerThread]] = collections.defaultdict(list)
    state = State()  # 全局状态

    def __init__(self, fn,
                 group: str = "OneStep",
                 name: str = None,
                 from_broker: Union[BaseBroker, List[BaseBroker], None] = None,
                 to_broker: Union[BaseBroker, List[BaseBroker], None] = None,
                 workers: Optional[int] = None,
                 middlewares: Optional[List[Any]] = None,
                 retry: Union[Callable, object] = NeverRetry(),
                 error_callback: Optional[Union[Callable, object]] = None):
        self.group = group
        self.fn = fn
        self.name = name or fn.__name__
        self.workers = workers or DEFAULT_WORKERS
        self.middlewares = middlewares or []

        self.from_brokers = self._init_broker(from_broker)
        self.to_brokers = self._init_broker(to_broker)
        self.retry = retry
        self.error_callback = error_callback

        for broker in self.from_brokers:
            self._add_consumer(broker)

    @staticmethod
    def _init_broker(broker: Union[BaseBroker, List[BaseBroker], None] = None):
        if not broker:
            return []

        if isinstance(broker, BaseBroker):
            return [broker]
        if isinstance(broker, (list, tuple)):
            return list(broker)
        raise TypeError(
            f"broker must be BaseBroker or list or tuple, not {type(broker)}")

    def _add_consumer(self, broker):
        for _ in range(self.workers):
            self.consumers[self.group].append(
                WorkerThread(onestep=self, broker=broker)
            )

    @classmethod
    def _find_consumers(cls, group: Optional[str] = None):
        """按组查找消费者"""
        if group is None:
            consumers = [c for v in cls.consumers.values() for c in v]
        else:
            consumers = cls.consumers[group]
        return consumers

    @classmethod
    def start(cls, group: Optional[str] = None):
        logger.debug(f"start: {group=}")
        for consumer in cls._find_consumers(group):
            consumer.start()
            logger.debug(f"started: {consumer=}")

    @classmethod
    def shutdown(cls, group: Optional[str] = None):
        logger.debug(f"stop: {group=}")
        for consumer in cls._find_consumers(group):
            consumer.shutdown()
            logger.debug(f"stopped: {consumer=}")

    def wraps(self, func):
        @functools.wraps(func)
        def wrapped_f(*args, **kwargs):
            return self(*args, **kwargs)  # noqa

        return wrapped_f

    def send(self, result, broker=None):
        """将返回的内容交给broker发送"""
        brokers = self._init_broker(broker) or self.to_brokers
        # 如果是Message类型，就不再封装
        message = result if isinstance(result, Message) else Message(body=result)
        self.before_emit("send", message=message)

        if result and not brokers:
            logger.debug("send(result): broker is empty")
            return

        if not result and brokers:
            logger.debug("send(result): body is empty")
            return

        for broker in brokers:
            message_sent.send(self, message=message, broker=broker)
            broker.send(message)
        self.after_emit("send", message=message)

    def before_emit(self, signal, *args, **kwargs):
        signal = "before_" + signal
        self.emit(signal, *args, **kwargs)

    def after_emit(self, signal, *args, **kwargs):
        signal = "after_" + signal
        self.emit(signal, *args, **kwargs)

    def emit(self, signal, *args, **kwargs):
        for middleware in self.middlewares:
            if not hasattr(middleware, signal):
                continue
            try:
                getattr(middleware, signal)(step=self, *args, **kwargs)
            except StopMiddleware as e:
                logger.debug(f"middleware<{middleware}> is stopped，reason: {e}")
                break


def decorator_func_proxy(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)

    return wrapper


class SyncOneStep(BaseOneStep):

    def __call__(self, *args, **kwargs):
        """同步执行原函数"""

        result = self.fn(*args, **kwargs)

        if isgenerator(result):
            for item in result:
                self.send(item)
        else:
            self.send(result)
        return result


class AsyncOneStep(BaseOneStep):

    async def __call__(self, *args, **kwargs):
        """"异步执行原函数"""

        if iscoroutinefunction(self.fn):
            result = await self.fn(*args, **kwargs)
        else:
            result = self.fn(*args, **kwargs)

        if isasyncgen(result):
            async for item in result:
                self.send(item)
        else:
            self.send(result)
        return result


class step:

    def __init__(self,
                 group: str = "OneStep",
                 name: str = None,
                 from_broker: Union[BaseBroker, List[BaseBroker], None] = None,
                 to_broker: Union[BaseBroker, List[BaseBroker], None] = None,
                 workers: Optional[int] = None,
                 middlewares: Optional[List[Any]] = None,
                 retry: Union[Callable, object] = NeverRetry(),
                 error_callback: Optional[Union[Callable, object]] = None):
        self.params = {
            "group": group,
            "name": name,
            "from_broker": from_broker,
            "to_broker": to_broker,
            "workers": workers,
            "middlewares": middlewares,
            "retry": retry,
            "error_callback": error_callback
        }

    def __call__(self, func, *_args, **_kwargs):
        if iscoroutinefunction(func) or isasyncgenfunction(func):
            os = AsyncOneStep(fn=func, **self.params)
        else:
            os = SyncOneStep(fn=func, **self.params)

        return os.wraps(func)

    @staticmethod
    def start(group=None, block=None):
        BaseOneStep.start(group=group)
        started.send()
        if block:
            import time
            while True:
                time.sleep(1)

    @staticmethod
    def shutdown(group=None):
        BaseOneStep.shutdown(group=group)
        stopped.send()

    @staticmethod
    def set_debugging():

        if not BaseOneStep.state.debug:
            onestep_logger = logging.getLogger("onestep")
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter(
                "[%(levelname)s] %(asctime)s %(name)s:%(message)s"))
            onestep_logger.addHandler(handler)
            onestep_logger.setLevel(logging.DEBUG)
            BaseOneStep.state.debug = True
