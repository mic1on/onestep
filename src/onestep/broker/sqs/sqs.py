import json
import threading
from queue import Queue
from typing import Optional, Dict, Any

from onestep.broker import BaseBroker, BaseConsumer
from onestep.message import Message
try:
    from .use_sqs import SQSStore
except ImportError:
    SQSStore = None


class _SQSMessage(Message):
    """SQS消息类"""

    @classmethod
    def from_broker(cls, broker_message: Any):
        """从SQS消息创建Message对象"""
        required_attrs = ("body", "delete", "message_id")
        if not all(hasattr(broker_message, attr) for attr in required_attrs):
            raise TypeError(
                f"Message object missing required SQS attributes: {required_attrs}"
            )

        try:
            message = json.loads(broker_message.body)
        except json.JSONDecodeError:
            message = {"body": broker_message.body}

        if not isinstance(message, dict):
            message = {"body": message}
        if "body" not in message:
            message = {"body": message}

        return cls(
            body=message.get("body"), extra=message.get("extra"), message=broker_message
        )


class SQSBroker(BaseBroker):
    """SQS消息队列Broker实现"""

    message_cls = _SQSMessage

    def __init__(
        self,
        queue_name: str,
        message_group_id: str,
        params: Optional[Dict] = None,
        prefetch: Optional[int] = 1,
        auto_create: bool = True,
        queue_params: Optional[Dict] = None,
        *args,
        **kwargs,
    ):
        """
        初始化SQS Broker

        :param queue_name: 队列名称
        :param message_group_id: 消息组ID (FIFO队列必需)
        :param params: SQS连接参数
        :param prefetch: 预取消息数量
        :param auto_create: 是否自动创建队列
        :param queue_params: 队列参数
        """
        super().__init__(*args, **kwargs)
        self.queue_name = queue_name
        self.queue = Queue()
        self.prefetch = prefetch
        self.message_group_id = message_group_id
        self.threads = []
        self._shutdown = False
        self._consuming_started = False
        self._consume_lock = threading.Lock()

        # 创建SQSStore实例
        if SQSStore is None:
            raise ImportError("SQSStore is not available. Please install `boto3`.")
        self.store = SQSStore(**(params or {}))

        # 确保队列存在
        if auto_create:
            self.store.declare_queue(queue_name, attributes=queue_params)

    def _consume(self, *args, **kwargs):
        """消费消息的内部方法"""
        prefetch = kwargs.pop("prefetch", self.prefetch)

        def callback(message):
            """处理接收到的消息"""
            self.queue.put(message)

        self.store.start_consuming(
            self.queue_name, callback=callback, prefetch=prefetch, **kwargs
        )

    def consume(self, *args, **kwargs) -> "SQSConsumer":
        """启动消费者"""
        daemon = kwargs.pop("daemon", True)
        timeout = kwargs.pop("timeout", 1000)
        with self._consume_lock:
            if not self._consuming_started:
                thread_kwargs = kwargs.copy()
                thread = threading.Thread(target=self._consume, args=args, kwargs=thread_kwargs)
                thread.daemon = daemon
                thread.start()
                self.threads.append(thread)
                self._consuming_started = True
        return SQSConsumer(self, timeout=timeout)

    def publish(self, message: Any, **kwargs):
        """发布消息"""
        self.store.send(
            self.queue_name,
            message=message,
            message_group_id=self.message_group_id,
            **kwargs,
        )

    def confirm(self, message: Message):
        """确认消息"""
        message.message.delete()

    def reject(self, message: Message):
        """拒绝消息"""
        message.message.delete()

    def requeue(self, message: Message, is_source: bool = False):
        """
        重新入队消息

        :param message: 消息对象
        :param is_source: 是否使用原始消息
        """
        if is_source:
            message.message.delete()
            self.store.send(
                self.queue_name,
                message.message.body,
                message_group_id=self.message_group_id,
            )
        else:
            message.message.delete()
            self.store.send(
                self.queue_name, message.body, message_group_id=self.message_group_id
            )

    def shutdown(self):
        """关闭Broker"""
        self._shutdown = True
        self.store.shutdown()
        for thread in self.threads:
            thread.join()
        self.queue = Queue()
        self.threads.clear()
        self._consuming_started = False


class SQSConsumer(BaseConsumer): ...
