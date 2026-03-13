from queue import Queue
from typing import Optional, Dict, Any, cast

try:
    from use_mysql import MysqlStore, Model, SQLModel
except ImportError:
    MysqlStore = None

from .base import BaseBroker, BaseConsumer
from ..message import Message


class MysqlBroker(BaseBroker):

    def __init__(
        self,
        params: Optional[Dict] = None,
        auto_create: Optional[bool] = True,
        *args,
        **kwargs
    ):
        if MysqlStore is None or Model is None:
            raise ImportError("未安装 use-mysql 依赖")

        super().__init__(*args, **kwargs)
        self.queue = Queue()
        params = params or {}
        auto_create = auto_create or False
        self.client = MysqlStore(generate_schemas=auto_create, **params)
        self.client.init()
        self._shutdown = False

    def send(self, message, *args, **kwargs):
        if not isinstance(message, Message):
            message = self.message_cls(body=message)
        return self.publish(message, *args, **kwargs)


    def publish(self, message: Any, **kwargs):
        body_raw = message.body if isinstance(message, Message) else message
        if not isinstance(body_raw, SQLModel):
            raise TypeError("MySQL Broker 仅支持 Model 类型的消息体")

        body = cast(SQLModel, body_raw)
        return self.client.create(body.__class__, **body.model_dump())

  
    def confirm(self, message: Message):
        return None

    def reject(self, message: Message):
        return None

    def requeue(self, message: Message, is_source=False):
        return None

    def shutdown(self):
        self._shutdown = True
        self.client.shutdown()


class MysqlConsumer(BaseConsumer):
    ...
