import json
import threading
from queue import Queue
from typing import Optional, Dict, Any, Callable

from onestep.broker import BaseBroker, BaseConsumer
from onestep.message import Message
try:
    from use_sqs import SQSStore
except ImportError:
    SQSStore = None
    boto3 = None



class _SQSMessage(Message):
    """SQS消息类"""

    @classmethod
    def from_broker(cls, broker_message: "boto3.resources.factory.sqs.Message"):
        """从SQS消息创建Message对象"""
        required_attrs = ("body", "delete", "message_id")
        if not all(hasattr(broker_message, attr) for attr in required_attrs):
            raise TypeError(
                f"Message object missing required SQS attributes: {required_attrs}"
            )

        try:
            # 如果body已经是dict，直接使用；如果是字符串，则解析JSON
            if isinstance(broker_message.body, dict):
                message = broker_message.body
            else:
                message = json.loads(broker_message.body)
        except (json.JSONDecodeError, TypeError):
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
        message_deduplication_id_func: Optional[Callable] = None,
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
        :param message_deduplication_id_func: 接收 msg_body(json_str) 作为参数，返回 str 类型的 message_deduplication_id
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
        self.message_deduplication_id_func = message_deduplication_id_func
        self.threads = []
        self._shutdown = False
        self._consuming_started = False
        self._consume_lock = threading.Lock()

        # 创建SQSStore实例
        if SQSStore is None:
            raise ImportError("Please install the `use-sqs` module to use SQSBroker")
        self.store = SQSStore(**(params or {}))

        # 确保队列存在
        if auto_create:
            self.store.declare_queue(queue_name, attributes=queue_params)

    def _consume(self, *args, **kwargs):
        """消费消息的内部方法"""
        prefetch = kwargs.pop("prefetch", self.prefetch)

        def callback(message):
            """处理接收到的消息"""
            # 直接将原始SQS消息放入队列，保留完整的消息引用
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
        if self.message_deduplication_id_func:
            message_deduplication_id = self.message_deduplication_id_func(message)
            if (
                isinstance(message_deduplication_id, str)
                and message_deduplication_id.strip()
                and len(message_deduplication_id.strip()) <= 128
            ):
                kwargs["message_deduplication_id"] = message_deduplication_id

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
        pass  # 拒绝消息时不删除，等待下一次消费 # 以支持 DLQ 机制

    def requeue(self, message: Message, is_source: bool = False):
        """
        重新入队消息

        :param message: 消息对象
        :param is_source: 是否使用原始消息
        """
        broker_msg = getattr(message, "message", None)
        # 确认原始消息，重新入队处理后的消息，以实现将消息放在队列尾部并等待处理
        if is_source and broker_msg is not None and hasattr(broker_msg, "body"):
            broker_msg.delete()
            self.store.send(
                self.queue_name,
                broker_msg.body,
                message_group_id=self.message_group_id,
            )
        else:
            broker_msg.delete()
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
