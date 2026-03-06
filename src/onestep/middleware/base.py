class DeprecationMeta(type):
    """中间件元类，用于检测废弃的方法"""

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

    定义中间件的标准接口，所有中间件必须继承此类并实现钩子方法。

    功能：
    - 消息发送前后的拦截（before_send/after_send）
    - 消息消费前后的拦截（before_consume/after_consume）
    - 支持执行顺序控制（order 参数）

    使用示例：
    ```python
    class LoggingMiddleware(BaseMiddleware):
        def __init__(self, order=50):
            super().__init__(order=order)

        def before_consume(self, step, message, *args, **kwargs):
            print(f"Processing message: {message.body}")

    @step(from_broker=broker, middlewares=[LoggingMiddleware()])
    def handler(message):
        return message.body
    ```

    执行顺序：
    - order 数值越小，越早执行
    - 默认 order=100

    钩子方法说明：
    - before_send: 消息发送前调用
    - after_send: 消息发送后调用
    - before_consume: 消息消费前调用
    - after_consume: 消息消费后调用

    中断中间件链：
    抛出 StopMiddleware 异常可以中断后续中间件的执行。
    """

    order: int = 100  # 默认顺序

    def __init__(self, order: int = 100):
        """
        初始化中间件

        :param order: 执行顺序（数值越小越早，默认: 100）
        """
        self.order = order

    def before_send(self, step, message, *args, **kwargs):
        """消息发送之前

        :param step: Step 实例（BaseOneStep）
        :param message: 要发送的 Message 对象
        :param args: 额外的位置参数
        :param kwargs: 额外的关键字参数
        """
        pass

    def after_send(self, step, message, *args, **kwargs):
        """消息发送之后

        :param step: Step 实例（BaseOneStep）
        :param message: 已发送的 Message 对象
        :param args: 额外的位置参数
        :param kwargs: 额外的关键字参数
        """
        pass

    def before_consume(self, step, message, *args, **kwargs):
        """消费消息之前

        :param step: Step 实例（BaseOneStep）
        :param message: 要消费的 Message 对象
        :param args: 额外的位置参数
        :param kwargs: 额外的关键字参数
        """
        pass

    def after_consume(self, step, message, *args, **kwargs):
        """消费消息之后

        :param step: Step 实例（BaseOneStep）
        :param message: 已消费的 Message 对象
        :param args: 额外的位置参数
        :param kwargs: 额外的关键字参数
        """
        pass

    def __repr__(self):
        """返回中间件的字符串表示，包含名称和 order 值"""
        return f"<{self.__class__.__name__} order={self.order}>"
