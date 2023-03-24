class BaseMiddleware:

    def before_send(self, message):
        """消息发送之前"""

    def after_send(self, message):
        """消息发送之后"""

    def before_receive(self):
        """消息接收之前"""

    def after_receive(self):
        """消息接收之后"""
