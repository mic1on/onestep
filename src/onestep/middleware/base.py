class BaseMiddleware:

    def before_send(self, step, message, *args, **kwargs):
        """消息发送之前"""
        pass

    def after_send(self, step, message, *args, **kwargs):
        """消息发送之后"""
        pass

    def before_receive(self, step, message, *args, **kwargs):
        """消息接收之前"""
        pass

    def after_receive(self, step, message, *args, **kwargs):
        """消息接收之后"""
        pass

    def __repr__(self):
        return f"<{self.__class__.__name__}>"
