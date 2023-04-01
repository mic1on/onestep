from .base import BaseMiddleware


class BaseConfigMiddleware(BaseMiddleware):

    def __init__(self, client, *args, **kwargs):
        self.config_key = kwargs.pop('config_key', None)
        self.client = client

    def process(self, step, message, *args, **kwargs):
        """实际获取配置的逻辑"""
        config = self.get(self.config_key)
        return config

    def get(self, key):
        raise NotImplementedError


class PublishConfigMixin:
    def before_receive(self, step, message, *args, **kwargs):
        """消息发送之前，给消息添加配置"""
        config = self.process(step, message, *args, **kwargs)  # noqa
        # 持久性，会跟随消息传递到其他 broker
        message.extra['config'] = config


class ConsumeConfigMixin:
    def before_receive(self, step, message, *args, **kwargs):
        """消息消费之前，给消息附加配置"""
        config = self.process(step, message, *args, **kwargs)  # noqa
        # 一次性，不跟随消息传递到其他 broker
        message.config = config


class NacosConfigMixin:

    def __init__(self, client, *args, **kwargs):
        super().__init__(client, *args, **kwargs)
        self.config_group = kwargs.pop("config_group", "DEFAULT_GROUP")

    def get(self, key):
        pass

    def set(self, key, value):
        pass


class NacosPublishConfigMiddleware(NacosConfigMixin, PublishConfigMixin, BaseConfigMiddleware):
    """发布前附加来自 Nacos 的配置"""


class NacosConsumeConfigMiddleware(NacosConfigMixin, ConsumeConfigMixin, BaseConfigMiddleware):
    """消费前附加来自 Nacos 的配置"""


class RedisConfigMixin:

    def __init__(self, client, *args, **kwargs):
        super().__init__(client, *args, **kwargs)
        self.config_group = kwargs.pop('config_group', 'onestep:config')

    def get(self, key):
        value = self.client.hget(name=self.config_group, key=key)  # noqa
        if isinstance(value, bytes):
            value = value.decode()
        return value

    def set(self, key, value):
        return self.client.hset(name=self.config_group, key=key, value=value)  # noqa


class RedisPublishConfigMiddleware(RedisConfigMixin, PublishConfigMixin, BaseConfigMiddleware):
    """发布前附加来自 Redis 的配置"""


class RedisConsumeConfigMiddleware(RedisConfigMixin, ConsumeConfigMixin, BaseConfigMiddleware):
    """消费前附加来自 Redis 的配置"""
