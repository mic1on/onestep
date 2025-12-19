import json
import os
import threading
import time
from typing import Callable, Optional, Union, Any, Dict

import boto3
from botocore.exceptions import ClientError
from loguru import logger


class SQSStore:
    """
    AWS SQS消息队列存储和消费类

    提供了与AWS SQS交互的各种方法,包括连接、声明队列、发送消息、获取消息数量和消费消息等。
    包含了重试机制和异常处理,以确保连接的可靠性和消息的正确传递。
    """

    MAX_RETRIES = 5
    MAX_RECONNECTION_DELAY = 32
    INITIAL_RECONNECTION_DELAY = 1

    def __init__(
        self,
        *,
        region_name: Optional[str] = None,
        aws_access_key_id: Optional[str] = None,
        aws_secret_access_key: Optional[str] = None,
        endpoint_url: Optional[str] = None,
        **kwargs,
    ):
        """
        初始化SQS客户端

        :param region_name: AWS region
        :param aws_access_key_id: AWS access key
        :param aws_secret_access_key: AWS secret key
        :param endpoint_url: SQS endpoint URL (用于本地测试)
        :param kwargs: 其他boto3参数
        """
        self._shutdown = False
        self._reconnection_delay = self.INITIAL_RECONNECTION_DELAY
        self._client = None
        self._queues = {}

        # 构建boto3参数
        self.parameters = {
            "region_name": region_name or os.environ.get("AWS_REGION", "us-east-1"),
            "aws_access_key_id": aws_access_key_id
            or os.environ.get("AWS_ACCESS_KEY_ID"),
            "aws_secret_access_key": aws_secret_access_key
            or os.environ.get("AWS_SECRET_ACCESS_KEY"),
        }
        if endpoint_url:
            self.parameters["endpoint_url"] = endpoint_url
        if kwargs:
            self.parameters.update(kwargs)

    @property
    def client(self):
        """获取或创建SQS客户端"""
        if self._client is None:
            self._client = boto3.resource("sqs", **self.parameters)
        return self._client

    def declare_queue(
        self,
        queue_name: str,
        attributes: Optional[Dict[str, str]] = None,
        auto_create: bool = True,
    ) -> Any:
        """
        声明或获取队列

        :param queue_name: 队列名称
        :param attributes: 队列属性
        :param auto_create: 是否自动创建
        :return: SQS队列对象
        """
        if queue_name in self._queues:
            return self._queues[queue_name]

        for attempt in range(self.MAX_RETRIES):
            try:
                queue = self.client.get_queue_by_name(QueueName=queue_name)
                self._queues[queue_name] = queue
                return queue
            except ClientError as e:
                if (
                    e.response["Error"]["Code"]
                    == "AWS.SimpleQueueService.NonExistentQueue"
                ):
                    if auto_create:
                        attributes = attributes or {}
                        if queue_name.endswith(".fifo"):
                            attributes.setdefault("FifoQueue", "true")
                            attributes.setdefault("ContentBasedDeduplication", "true")
                        queue = self.client.create_queue(
                            QueueName=queue_name, Attributes=attributes
                        )
                        self._queues[queue_name] = queue
                        return queue
                    raise
                elif attempt < self.MAX_RETRIES - 1:
                    delay = min(
                        self._reconnection_delay * (2**attempt),
                        self.MAX_RECONNECTION_DELAY,
                    )
                    logger.warning(
                        f"Failed to get queue, retrying in {delay}s...error={e}"
                    )
                    time.sleep(delay)
                    continue
                raise

    def send(
        self,
        queue_name: str,
        message: Union[str, bytes, dict],
        message_group_id: Optional[str] = None,
        **kwargs,
    ) -> Any:
        """
        发送消息到指定队列

        :param queue_name: 队列名称
        :param message: 消息内容
        :param message_group_id: 消息组ID (FIFO队列必需)
        :param kwargs: 其他发送参数
        :return: 发送的消息
        """
        if isinstance(message, (str, bytes)):
            message_body = message
        else:
            message_body = json.dumps(message)

        queue = self.declare_queue(queue_name)
        send_params = kwargs.copy()

        # 处理FIFO队列的特殊参数
        if queue_name.endswith(".fifo"):
            if not message_group_id:
                message_group_id = queue_name.rsplit(".", 1)[0]
            send_params["MessageGroupId"] = message_group_id

        for attempt in range(self.MAX_RETRIES):
            try:
                result = queue.send_message(MessageBody=message_body, **send_params)
                return message
            except Exception as e:
                if attempt < self.MAX_RETRIES - 1:
                    delay = min(
                        self._reconnection_delay * (2**attempt),
                        self.MAX_RECONNECTION_DELAY,
                    )
                    logger.warning(
                        f"Failed to send message, retrying in {delay}s...error={e}"
                    )
                    time.sleep(delay)
                    continue
                raise

    def get_message_counts(self, queue_name: str) -> int:
        """
        获取队列中的消息数量

        :param queue_name: 队列名称
        :return: 消息数量
        """
        try:
            queue = self.declare_queue(queue_name)
            attrs = queue.attributes
            return int(attrs.get("ApproximateNumberOfMessages", 0))
        except Exception as e:
            logger.error(f"Failed to get message count: {e}")
            return 0

    def flush_queue(self, queue_name: str):
        """
        清空队列中的所有消息

        :param queue_name: 队列名称
        """
        queue = self.declare_queue(queue_name)
        while True:
            messages = queue.receive_messages(MaxNumberOfMessages=10)
            if not messages:
                break
            for msg in messages:
                msg.delete()

    def start_consuming(
        self,
        queue_name: str,
        callback: Callable,
        prefetch: int = 1,
        no_ack: bool = False,
        **kwargs,
    ):
        """
        开始消费队列消息

        :param queue_name: 队列名称
        :param callback: 消息处理回调函数
        :param prefetch: 预取消息数量
        :param no_ack: 是否自动确认
        :param kwargs: 其他参数
        """
        self._shutdown = False
        reconnection_delay = self.INITIAL_RECONNECTION_DELAY
        queue = self.declare_queue(queue_name)

        while not self._shutdown:
            try:
                # 从kwargs中移除no_ack参数,因为SQS API不支持这个参数
                sqs_kwargs = {k: v for k, v in kwargs.items() if k != "no_ack"}

                messages = queue.receive_messages(
                    MaxNumberOfMessages=prefetch,
                    AttributeNames=["All"],
                    WaitTimeSeconds=20,  # 使用长轮询
                    **sqs_kwargs,
                )

                for message in messages:
                    try:
                        body = message.body
                        try:
                            body = json.loads(body)
                        except json.JSONDecodeError:
                            pass
                        callback(message)

                        # 根据no_ack参数决定是否自动删除消息
                        if no_ack:
                            message.delete()
                    except Exception as e:
                        logger.exception(f"Error processing message: {e}")

                if not messages:
                    time.sleep(1)  # 避免空队列时频繁请求

            except Exception as e:
                if self._shutdown:
                    break
                logger.exception(f"SQSStore consume error: {e}, reconnecting...")
                self._client = None
                time.sleep(reconnection_delay)
                reconnection_delay = min(
                    reconnection_delay * 2, self.MAX_RECONNECTION_DELAY
                )
                queue = self.declare_queue(queue_name)

    def listener(self, queue_name: str, no_ack: bool = False, **kwargs):
        """
        消息监听器装饰器

        :param queue_name: 队列名称
        :param no_ack: 是否自动确认
        :param kwargs: 其他参数
        """

        def wrapper(callback: Callable[[Any], Any]):
            logger.info(f"Starting SQS consumer for queue: {queue_name}")

            def target():
                self.start_consuming(queue_name, callback, no_ack=no_ack, **kwargs)

            thread = threading.Thread(target=target)
            thread.daemon = True
            thread.start()
            return thread

        return wrapper

    def shutdown(self):
        """关闭连接"""
        self._shutdown = True
        self._client = None
        self._queues.clear()


class SQSListener:
    """SQS监听器类"""

    def __init__(
        self, instance: SQSStore, *, queue_name: str, no_ack: bool = False, **kwargs
    ):
        """
        初始化监听器

        :param instance: SQSStore实例
        :param queue_name: 队列名称
        :param no_ack: 是否自动确认
        :param kwargs: 其他参数
        """
        self.instance = instance
        self.queue_name = queue_name
        self.no_ack = no_ack
        self.kwargs = kwargs

    def __call__(self, callback: Callable[[Any], None]):
        """
        调用监听器

        :param callback: 回调函数
        :return: 监听线程
        """
        listener = self.instance.listener(self.queue_name, self.no_ack, **self.kwargs)
        return listener(callback)


# 别名
useSQS = SQSStore
useSQSListener = SQSListener
