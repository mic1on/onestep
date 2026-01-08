from typing import TYPE_CHECKING, Any, Protocol

from .base import BaseMiddleware

if TYPE_CHECKING:

    class _HasProcessMethod(Protocol):
        def process(self, step: Any, message: Any, *args: Any, **kwargs: Any) -> Any: ...


    class _HasClient(Protocol):
        client: Any


class BaseConfigMiddleware(BaseMiddleware):

    def __init__(self, client: Any, *args: Any, **kwargs: Any) -> None:
        self.config_key = kwargs.pop('config_key', None)
        self.client = client

    def process(self, step: Any, message: Any, *args: Any, **kwargs: Any) -> Any:
        """实际获取配置的逻辑"""
        config = self.get(self.config_key)
        return config

    def get(self, key: Any) -> Any:
        raise NotImplementedError


class PublishConfigMixin:
    def before_send(self, step: Any, message: Any, *args: Any, **kwargs: Any) -> None:
        """消息发送之前，给消息添加配置"""
        # This mixin is designed to be used with BaseConfigMiddleware
        # which provides the process method
        config = self.process(step, message, *args, **kwargs)  # type: ignore[attr-defined]
        # 持久性，会跟随消息传递到其他 broker
        message.extra['config'] = config


class ConsumeConfigMixin:
    def before_consume(self, step: Any, message: Any, *args: Any, **kwargs: Any) -> None:
        """消息消费之前，给消息附加配置"""
        # This mixin is designed to be used with BaseConfigMiddleware
        # which provides the process method
        config = self.process(step, message, *args, **kwargs)  # type: ignore[attr-defined]
        # 一次性，不跟随消息传递到其他 broker
        message.config = config


class NacosConfigMixin:

    def __init__(self, client: Any, *args: Any, **kwargs: Any) -> None:
        # This mixin is designed to be used with BaseConfigMiddleware
        # which calls super().__init__
        super().__init__(client, *args, **kwargs)  # type: ignore[call-arg]
        self.config_group = kwargs.pop("config_group", "DEFAULT_GROUP")

    def get(self, key: Any) -> Any:
        pass

    def set(self, key: Any, value: Any) -> None:
        pass


class NacosPublishConfigMiddleware(NacosConfigMixin, PublishConfigMixin, BaseConfigMiddleware):
    """发布前附加来自 Nacos 的配置"""


class NacosConsumeConfigMiddleware(NacosConfigMixin, ConsumeConfigMixin, BaseConfigMiddleware):
    """消费前附加来自 Nacos 的配置"""


class RedisConfigMixin:

    def __init__(self, client: Any, *args: Any, **kwargs: Any) -> None:
        # This mixin is designed to be used with BaseConfigMiddleware
        # which calls super().__init__ and sets self.client
        super().__init__(client, *args, **kwargs)  # type: ignore[call-arg]
        self.config_group = kwargs.pop('config_group', 'onestep:config')

    def get(self, key: Any) -> Any:
        value = self.client.hget(name=self.config_group, key=key)  # type: ignore[attr-defined]
        if isinstance(value, bytes):
            value = value.decode()
        return value

    def set(self, key: Any, value: Any) -> Any:
        return self.client.hset(name=self.config_group, key=key, value=value)  # type: ignore[attr-defined]


class RedisPublishConfigMiddleware(RedisConfigMixin, PublishConfigMixin, BaseConfigMiddleware):
    """发布前附加来自 Redis 的配置"""


class RedisConsumeConfigMiddleware(RedisConfigMixin, ConsumeConfigMixin, BaseConfigMiddleware):
    """消费前附加来自 Redis 的配置"""
