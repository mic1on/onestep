"""
将指定的函数放入线程中运行
"""
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Iterable

import logging
import threading
from asyncio import iscoroutinefunction
from inspect import isasyncgenfunction

from asgiref.sync import async_to_sync

from .message import Message
from .retry import RetryStatus
from .broker import BaseBroker
from .exception import DropMessage
from .signal import message_received, message_consumed, message_error, message_drop

logger = logging.getLogger(__name__)


class BaseWorker:
    broker_exit: Dict[BaseBroker, bool] = {}
    broker_exit_lock = threading.Lock()

    def __init__(self, onestep, broker: BaseBroker, *args, **kwargs):
        self.instance = onestep
        self.retry = self.instance.retry
        self.error_callback = self.instance.error_callback
        self.broker = broker
        self.args = args
        self.kwargs = kwargs
        self._shutdown = False

    @property
    def instance_name(self):
        return self.instance.fn.__name__

    def start(self):
        """启动 Worker"""
        raise NotImplementedError

    def run(self):
        """执行 Worker 的逻辑"""
        raise NotImplementedError

    def shutdown(self):
        """关闭 Worker"""
        raise NotImplementedError

    def receive_messages(self) -> Iterable[Message]:
        """ 从broker中获取消息 """
        for result in self.broker.consume():
            if self._shutdown:
                break
            if result is None:
                continue
            messages = result if isinstance(result, Iterable) else [result]
            yield from messages
            # when broker is once, it will shut down after receive a message
            if self.broker.once:
                self.shutdown()

    def _run_real_instance(self, message: Message) -> None:
        """ 执行实例的逻辑 """
        if iscoroutinefunction(self.instance.fn) or isasyncgenfunction(self.instance.fn):
            async_to_sync(self.instance)(message, *self.args, **self.kwargs)
        else:
            self.instance(message, *self.args, **self.kwargs)

    def handle_message(self, message: Message):
        """ 处理消息 """
        message.broker = message.broker or self.broker
        logger.debug(f"{self.instance.name} receive message<{message}> from {self.broker!r}")
        message_received.send(self, message=message)
        try:
            self.instance.before_emit("consume", message=message)

            self._run_real_instance(message)
            message_consumed.send(self, message=message)
            message.confirm()

            self.instance.after_emit("consume", message=message)
        except DropMessage as e:
            message_drop.send(self, message=message, reason=e)
            logger.warning(f"{self.instance.name} dropped <{type(e).__name__}: {str(e)}>")
            message.reject()
        except Exception as e:
            message_error.send(self, message=message, error=e)
            if self.instance.state.debug:
                logger.exception(f"{self.instance.name} run error <{type(e).__name__}: {str(e)}>")
            else:
                logger.error(f"{self.instance.name} run error <{type(e).__name__}: {str(e)}>")
            message.set_exception()

            retry_status = self.retry(message)
            if retry_status is RetryStatus.END_WITH_CALLBACK:
                if self.error_callback:
                    self.error_callback(message)
                message.reject()
            elif retry_status is RetryStatus.END_IGNORE_CALLBACK:
                # 由于是队列内重试，不会触发错误回调
                message.requeue()
        finally:
            # When message is triggered by cancel_consume, it will be shutdown
            if self.broker.cancel_consume and self.broker.cancel_consume(message):
                self.shutdown()

    def __repr__(self):
        return f"<{self.__class__.__name__} {self.instance.name}>"


class ThreadWorker(BaseWorker):

    def __init__(self, onestep, broker: BaseBroker, *args, **kwargs):
        """
        线程执行包装过的`onestep`函数
        :param onestep: OneStep实例
        :param broker: 监听的from broker
        """
        super().__init__(onestep, broker, *args, **kwargs)
        self.thread = None

    def start(self):
        """启动单线程 Worker"""
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        """线程执行包装过的`onestep`函数

        `fn`为`onestep`函数，执行会调用`onestep`的`__call__`方法
        :return:
        """

        while not self._shutdown:
            with ThreadWorker.broker_exit_lock:
                if ThreadWorker.broker_exit.get(self.broker, False):
                    self.shutdown()
                    break
            for message in self.receive_messages():
                self.handle_message(message)

    def shutdown(self):
        ThreadWorker.broker_exit[self.broker] = True
        self.broker.shutdown()
        self._shutdown = True


class ThreadPoolWorker(BaseWorker):

    def __init__(self, onestep, broker: BaseBroker, workers=None, *args, **kwargs):
        super().__init__(onestep, broker, *args, **kwargs)
        self.executor = ThreadPoolExecutor(max_workers=workers)

    def start(self):
        """启动线程池 Worker"""
        self.executor.submit(self.run)

    def run(self):
        """线程执行包装过的`onestep`函数

        `fn`为`onestep`函数，执行会调用`onestep`的`__call__`方法
        :return:
        """

        while not self._shutdown:
            with ThreadPoolWorker.broker_exit_lock:
                if ThreadPoolWorker.broker_exit.get(self.broker, False):
                    self.shutdown()
                    break
            for message in self.receive_messages():
                self.handle_message(message)

    def shutdown(self):
        """关闭线程池 Worker"""
        ThreadPoolWorker.broker_exit[self.broker] = True
        self._shutdown = True
        self.executor.shutdown()
