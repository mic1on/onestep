import functools
import collections
import inspect
import logging

from inspect import isgenerator, iscoroutinefunction, isasyncgenfunction, isasyncgen
from itertools import groupby
from typing import Optional, List, Dict, Any, Callable, Union, Type

from .broker.base import BaseBroker
from .exception import StopMiddleware
from .message import Message
from .retry import TimesRetry
from .signal import message_sent, started, stopped
from .state import State
from .worker import ThreadWorker, BaseWorker

logger = logging.getLogger(__name__)

MAX_WORKERS = 20
DEFAULT_WORKERS = 1
DEFAULT_WORKER_CLASS = ThreadWorker


class BaseOneStep:
    consumers: Dict[str, List[BaseWorker]] = collections.defaultdict(list)
    state = State()  # 全局状态

    def __init__(self, fn,
                 group: str = "OneStep",
                 name: str = None,
                 from_broker: Union[BaseBroker, List[BaseBroker], None] = None,
                 to_broker: Union[BaseBroker, List[BaseBroker], None] = None,
                 workers: Optional[int] = None,
                 worker_class: Optional[Type[BaseWorker]] = None,
                 middlewares: Optional[List[Any]] = None,
                 retry: Union[Callable, object] = TimesRetry(),
                 error_callback: Optional[Union[Callable, object]] = None):
        self.group = group
        self.fn = fn
        self.name = name or fn.__name__
        self.workers = workers or DEFAULT_WORKERS
        self.worker_class = worker_class or DEFAULT_WORKER_CLASS
        if self.workers > MAX_WORKERS:
            logger.warning(f"workers[{self.workers}] litter than {MAX_WORKERS}")
            self.workers = MAX_WORKERS
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
        """ 添加来源消费者 """
        worker_class_params = inspect.signature(self.worker_class.__init__).parameters
        if "workers" in worker_class_params:
            self.consumers[self.group].append(
                self.worker_class(onestep=self, broker=broker, workers=self.workers)
            )
        else:
            for _ in range(self.workers):
                self.consumers[self.group].append(
                    self.worker_class(onestep=self, broker=broker)
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
    def print_jobs(cls, group):
        print("Jobs:")
        prints = []
        _consumers = cls._find_consumers(group)
        group_instance = groupby(_consumers, key=lambda x: x.instance)
        for instance, _ in group_instance:
            prints.append([instance.name, instance.group, instance.workers, str(instance.from_brokers)])
        print("{:<15} {:<10} {:<10} {:<20}".format("Job", "Group", "Workers", "From Brokers"))
        for v in prints:
            print("{:<15} {:<10} {:<10} {:<20}".format(*v))

    @classmethod
    def start(cls, group: Optional[str] = None, print_jobs: bool = False):
        logger.debug(f"start group [{group or 'all'}]")
        _consumers = cls._find_consumers(group)
        if not _consumers:
            logger.debug(f"no consumer found in group [{group or 'all'}]")
            return
        if print_jobs:
            cls.print_jobs(group)
        for consumer in _consumers:
            consumer.start()
            logger.debug(f"started: {consumer=}")

    @classmethod
    def shutdown(cls, group: Optional[str] = None):
        logger.debug(f"stop group [{group or 'all'}]")
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

    @classmethod
    def is_shutdown(cls, group):
        # check all broker
        _consumers = cls._find_consumers(group)
        if not _consumers:
            return True
        return all(broker._shutdown for broker in _consumers)


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
                 *,
                 group: str = "OneStep",
                 name: str = None,
                 from_broker: Union[BaseBroker, List[BaseBroker], None] = None,
                 to_broker: Union[BaseBroker, List[BaseBroker], None] = None,
                 workers: Optional[int] = None,
                 worker_class: Optional[Type[BaseWorker]] = None,
                 middlewares: Optional[List[Any]] = None,
                 retry: Union[Callable, object] = TimesRetry(),
                 error_callback: Optional[Union[Callable, object]] = None):
        self.params = {
            "group": group,
            "name": name,
            "from_broker": from_broker,
            "to_broker": to_broker,
            "workers": workers,
            "worker_class": worker_class,
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
    def start(group=None, block=None, print_jobs=False):
        BaseOneStep.start(group=group, print_jobs=print_jobs)
        started.send()
        if block:
            import time
            while not BaseOneStep.is_shutdown(group):
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
