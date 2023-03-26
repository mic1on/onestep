"""
将指定的函数放入线程中运行
"""
import logging
import threading
from asyncio import iscoroutinefunction
from inspect import isasyncgenfunction

from asgiref.sync import async_to_sync

from onestep.broker import BaseBroker
from onestep.message import Message
from onestep.signal import message_received, message_consumed, message_error

logger = logging.getLogger(__name__)


class WorkerThread(threading.Thread):

    def __init__(self, fn, broker: BaseBroker, *args, **kwargs):
        super().__init__(daemon=True)
        self.instance = fn
        self.retry = self.instance.retry
        self.error_callback = self.instance.error_callback
        self.broker = broker
        self.args = args
        self.kwargs = kwargs
        self.__shutdown = False
        self.__shutdown_event = threading.Event()

    def run(self):
        """线程执行包装过的`onestep`函数

        `fn`为`onestep`函数，执行会调用`onestep`的`__call__`方法
        :return:
        """
        self.__shutdown_event.clear()

        while not self.__shutdown:
            if self.__shutdown:
                break
            # TODO：consume应当传入一些配置参数
            for message in self.broker.consume():
                if message is None:
                    continue
                logger.debug(f"receive message<{message}>")
                message_received.send(self, message=message)
                if isinstance(message, Message):
                    # 如果是Message类型，就不再封装，并且更新来源broker
                    message.replace(broker=self.broker)
                else:
                    message = Message(message, self.broker)
                self.instance.before_emit("receive", message)
                self._run_instance(message)
                self.instance.after_emit("receive", message)

    def shutdown(self):
        self.__shutdown = True
        self.__shutdown_event.wait()

    def _run_instance(self, message):
        while True:
            try:
                if message.fail and not self.retry(message):
                    if self.error_callback:
                        self.error_callback(message)
                    return
                if iscoroutinefunction(self.instance.fn) or isasyncgenfunction(self.instance.fn):
                    async_to_sync(self.instance)(message, *self.args, **self.kwargs)
                else:
                    self.instance(message, *self.args, **self.kwargs)

            except Exception as e:
                message_error.send(self, message=message, error=e)
                logger.exception(f"{self.instance.fn.__name__} run error<{type(e).__name__}: {str(e)}>")
                message.set_exception(e)
            else:
                message_consumed.send(self, message=message)
                return message.ack()
