import redis

from onestep.middleware.base import BaseMiddleware


class BaseConfigMiddleware(BaseMiddleware):

    def __init__(self, client, *args, **kwargs):
        self.config_key = kwargs.pop('config_key')
        self.client = client

    def before_send(self, message):
        """消息发送之前，给消息添加配置"""
        config = self.get(self.config_key)
        message.config = config

    def get(self, key):
        raise NotImplementedError


class NacosConfigMiddleware(BaseConfigMiddleware):

    def __init__(self, client, *args, **kwargs):
        super().__init__(client, *args, **kwargs)
        self.config_group = kwargs.pop("config_group", "DEFAULT_GROUP")


class RedisConfigMiddleware(BaseConfigMiddleware):

    def __init__(self, client, *args, **kwargs):
        super().__init__(client, *args, **kwargs)
        self.config_group = kwargs.pop('config_group', 'onestep:config')

    def get(self, key):
        value = self.client.hget(name=self.config_group, key=key)
        if isinstance(value, bytes):
            value = value.decode()
        return value

    def set(self, key, value):
        return self.client.hset(name=self.config_group, key=key, value=value)


if __name__ == '__main__':
    rds_params = {
    }
    rds_client = redis.Redis(**rds_params)

    rc = RedisConfigMiddleware(client=rds_client)
    print(rc.get("name"))
