"""
将指定的函数放入线程中运行
"""
import logging
import threading
from asyncio import iscoroutinefunction
from inspect import isasyncgenfunction

from asgiref.sync import async_to_sync

from .broker import BaseBroker
from .exception import DropMessage
from .message import Message
from .signal import message_received, message_consumed, message_error

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
                self.instance.before_emit("receive", message)
                self._run_instance(message)
                self.instance.after_emit("receive", message)

    def shutdown(self):
        self.__shutdown = True
        self.__shutdown_event.wait()

    def _run_instance(self, message):
        while True:
            try:
                if message.fail:
                    retry_state = self.retry(message)  # True=继续（执行重试）
                    if not retry_state:  # False=结束（执行回调），None=结束（忽略回调）
                        if retry_state is False and self.error_callback:
                            self.error_callback(message)
                        return message.nack(requeue=False)  # 返回前 nack 消息
                if iscoroutinefunction(self.instance.fn) or isasyncgenfunction(self.instance.fn):
                    async_to_sync(self.instance)(message, *self.args, **self.kwargs)
                else:
                    self.instance(message, *self.args, **self.kwargs)

            except DropMessage as e:
                logger.warning(f"{self.instance.fn.__name__} droped <{type(e).__name__}: {str(e)}>")
                return message.nack()

            except Exception as e:
                message_error.send(self, message=message, error=e)
                if self.instance.state.debug:
                    logger.exception(f"{self.instance.fn.__name__} run error<{type(e).__name__}: {str(e)}>")
                else:
                    logger.error(f"{self.instance.fn.__name__} run error<{type(e).__name__}: {str(e)}>")
                message.set_exception(e)

            else:
                message_consumed.send(self, message=message)
                return message.ack()
