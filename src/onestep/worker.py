"""
将指定的函数放入线程中运行
"""

try:
    from collections import Iterable
except ImportError:
    from collections.abc import Iterable
import logging
import threading
from asyncio import iscoroutinefunction
from inspect import isasyncgenfunction

from asgiref.sync import async_to_sync

from .retry import RetryStatus
from .broker import BaseBroker
from .exception import DropMessage
from .signal import message_received, message_consumed, message_error, message_drop

logger = logging.getLogger(__name__)


class WorkerThread(threading.Thread):

    def __init__(self, onestep, broker: BaseBroker, *args, **kwargs):
        """
        线程执行包装过的`onestep`函数
        :param onestep: OneStep实例
        :param broker: 监听的from broker
        """
        super().__init__(daemon=True)
        self.instance = onestep
        self.retry = self.instance.retry
        self.error_callback = self.instance.error_callback
        self.broker = broker
        self.args = args
        self.kwargs = kwargs
        self._shutdown = False

    def run(self):
        """线程执行包装过的`onestep`函数

        `fn`为`onestep`函数，执行会调用`onestep`的`__call__`方法
        :return:
        """

        while not self._shutdown:
            # TODO：consume应当传入一些配置参数
            for result in self.broker.consume():
                if self._shutdown:
                    break
                if result is None:
                    continue
                messages = (
                    result
                    if isinstance(result, Iterable)
                    else [result]
                )
                for message in messages:
                    message.broker = message.broker or self.broker
                    logger.debug(f"{self.instance.name} receive message<{message}> from {self.broker!r}")
                    message_received.send(self, message=message)
                    try:
                        self.instance.before_emit("consume", message=message)
                        self._run_instance(message)
                        self.instance.after_emit("consume", message=message)
                    except DropMessage as e:
                        message_drop.send(self, message=message, reason=e)
                        logger.warning(f"{self.instance.name} dropped <{type(e).__name__}: {str(e)}>")
                        message.reject()
                    finally:
                        if self.broker.cancel_consume and self.broker.cancel_consume(message):
                            self.shutdown()
                else:
                    if self.broker.once:
                        self.shutdown()

    def shutdown(self):
        self.broker.shutdown()
        self._shutdown = True

    def _run_instance(self, message):
        while True:
            try:
                if iscoroutinefunction(self.instance.fn) or isasyncgenfunction(self.instance.fn):
                    async_to_sync(self.instance)(message, *self.args, **self.kwargs)
                else:
                    self.instance(message, *self.args, **self.kwargs)
                message_consumed.send(self, message=message)
                return message.confirm()
            except Exception as e:
                message_error.send(self, message=message, error=e)
                if self.instance.state.debug:
                    logger.exception(f"{self.instance.name} run error <{type(e).__name__}: {str(e)}>")
                else:
                    logger.error(f"{self.instance.name} run error <{type(e).__name__}: {str(e)}>")
                message.set_exception()

                retry_status = self.retry(message)
                if retry_status is RetryStatus.CONTINUE:
                    continue
                elif retry_status is RetryStatus.END_WITH_CALLBACK:
                    if self.error_callback:
                        self.error_callback(message)
                    return message.reject()
                else:  # RetryStatus.END_IGNORE_CALLBACK
                    # 由于是队列内重试，不会触发错误回调
                    return message.requeue()
