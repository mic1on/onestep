class DeprecationMeta(type):

    def __new__(cls, name, bases, attrs):
        if "before_receive" in attrs or "after_receive" in attrs:
            raise DeprecationWarning(
                f"`{name}` "
                "The before_receive and after_receive methods are deprecated, "
                "please use before_consume and after_consume instead"
            )
        return super().__new__(cls, name, bases, attrs)


class BaseMiddleware(metaclass=DeprecationMeta):
    """中间件基类

    支持通过 `order` 参数控制执行顺序。
    数值越小，越早执行。
    """

    order: int = 100  # 默认顺序

    def __init__(self, order: int = 100):
        """
        初始化中间件

        :param order: 执行顺序（数值越小越早，默认: 100）
        """
        self.order = order

    def before_send(self, step, message, *args, **kwargs):
        """消息发送之前"""
        pass

    def after_send(self, step, message, *args, **kwargs):
        """消息发送之后"""
        pass

    def before_consume(self, step, message, *args, **kwargs):
        """消费消息之前"""
        pass

    def after_consume(self, step, message, *args, **kwargs):
        """消费消息之后"""
        pass

    def __repr__(self):
        return f"<{self.__class__.__name__} order={self.order}>"
