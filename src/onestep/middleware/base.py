class BaseMiddleware:

    def before_send(self, *args, **kwargs):
        """消息发送之前"""

    def after_send(self, *args, **kwargs):
        """消息发送之后"""

    def before_receive(self, *args, **kwargs):
        """消息接收之前"""

    def after_receive(self, *args, **kwargs):
        """消息接收之后"""
