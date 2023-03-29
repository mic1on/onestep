import logging
from threading import local
import amqpstorm
from amqpstorm.exception import AMQPConnectionError

MAX_SEND_ATTEMPTS = 6  # 最大发送重试次数
MAX_CONNECTION_ATTEMPTS = float('inf')  # 最大连接重试次数

logger = logging.Logger(__name__)


class RabbitmqStore:

    def __init__(self, *, confirm_delivery=True, host=None, port=None, username=None, password=None,
                 **kwargs):
        """
        :param confirm_delivery: 是否开启消息确认
        :param host: RabbitMQ host
        :param port: RabbitMQ port
        :param username: RabbitMQ username
        :param password: RabbitMQ password
        :param kwargs: RabbitMQ parameters
        """
        self.state = local()
        self.parameters = {
            'hostname': host or 'localhost',
            'port': port or 5672,
            'username': username or 'guest',
            'password': password or 'guest',
        }
        if kwargs:
            self.parameters.update(kwargs)
        self.confirm_delivery = confirm_delivery

    def _create_connection(self):
        attempts = 1
        while attempts <= MAX_CONNECTION_ATTEMPTS:
            try:
                return amqpstorm.Connection(**self.parameters)
            except AMQPConnectionError as exc:
                attempts += 1
                logger.warning("RabbitmqStore connection error: %s", exc)
        raise AMQPConnectionError("RabbitmqStore connection error, max attempts reached")

    @property
    def connection(self):
        connection = getattr(self.state, "connection", None)
        if connection is None or not connection.is_open:
            connection = self.state.connection = self._create_connection()
        return connection

    @connection.deleter
    def connection(self):
        if _connection := getattr(self.state, "connection", None):
            _connection.close()
            del self.state.connection
            del self.state.channel

    @property
    def channel(self):
        connection = getattr(self.state, "connection", None)
        channel = getattr(self.state, "channel", None)
        if all([connection, channel]) and all([connection.is_open, channel.is_open]):
            return channel
        channel = self.state.channel = self.connection.channel()
        if self.confirm_delivery:
            channel.confirm_deliveries()
        return channel

    def declare_queue(self, queue_name, arguments=None):
        """声明队列"""
        if arguments is None:
            arguments = {}
        return self.channel.queue.declare(queue_name, durable=True, arguments=arguments)

    def send(self, queue_name, message, priority=None, **kwargs):
        """发送消息"""
        attempts = 1
        while True:
            try:
                self.declare_queue(queue_name)
                self.channel.basic.publish(
                    message, queue_name, properties=priority, **kwargs
                )
                return message
            except Exception as exc:
                del self.connection
                attempts += 1
                if attempts > MAX_SEND_ATTEMPTS:
                    raise exc

    def flush_queue(self, queue_name):
        """清空队列"""
        self.channel.queue.purge(queue_name)

    def get_message_counts(self, queue_name: str) -> int:
        """获取消息数量"""
        queue_response = self.declare_queue(queue_name)
        return queue_response.get("message_count", 0)

    def start_consuming(self, queue_name, callback, prefetch=1, **kwargs):
        """开始消费"""
        while True:
            try:
                self.channel.basic.qos(prefetch_count=prefetch)
                self.channel.basic.consume(queue=queue_name, callback=callback, no_ack=False, **kwargs)
                self.channel.start_consuming(to_tuple=False)
            except AMQPConnectionError:
                logger.warning("RabbitmqStore consume connection error, reconnecting...")
                del self.connection
            except Exception as e:
                logger.exception(f"RabbitmqStore consume error<{e}>, reconnecting...")
                del self.connection
