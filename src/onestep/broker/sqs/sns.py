from typing import Optional, Dict, Any

from onestep.broker import BaseBroker

try:
    from use_sqs import SNSPublisher
except ImportError:
    SNSPublisher = None


class SNSBroker(BaseBroker):
    """SNS消息Broker实现"""

    def __init__(
        self,
        topic_arn: str,
        message_group_id: Optional[str] = None,
        params: Optional[Dict] = None,
        *args,
        **kwargs,
    ):
        """
        初始化SNS Broker

        :param topic_arn: SNS主题ARN
        :param message_group_id: 消息组ID (FIFO主题必需)
        :param params: SNS连接参数
        """
        if SNSPublisher is None:
            raise ImportError("Please install the `use-sqs` module to use SNSBroker")

        super().__init__(*args, **kwargs)
        self.topic_arn = topic_arn
        self.message_group_id = message_group_id

        # 创建SNSPublisher实例
        self.publisher = SNSPublisher(**(params or {}))

    def publish(self, message: Any, **kwargs):
        """发布消息"""
        # 如果初始化时设置了message_group_id，且调用时未指定，则使用初始化的值
        if self.message_group_id and "message_group_id" not in kwargs:
            kwargs["message_group_id"] = self.message_group_id

        self.publisher.publish(
            self.topic_arn,
            message=message,
            **kwargs,
        )

    def consume(self, *args, **kwargs):
        """消费消息 - SNS不支持直接消费"""
        raise NotImplementedError("SNS Broker does not support consumption")
