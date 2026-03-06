"""中间件优先级和 MemoryBroker 默认值测试"""
import time
from onestep import step, MemoryBroker
from onestep.middleware import BaseMiddleware


def test_middleware_order():
    """测试中间件按 order 参数排序执行"""

    execution_order = []

    class FirstMiddleware(BaseMiddleware):
        def __init__(self):
            super().__init__(order=10)  # 最先执行

        def before_consume(self, message, step):
            execution_order.append("first")

    class SecondMiddleware(BaseMiddleware):
        def __init__(self):
            super().__init__(order=50)  # 中间执行

        def before_consume(self, message, step):
            execution_order.append("second")

    class ThirdMiddleware(BaseMiddleware):
        def __init__(self):
            super().__init__(order=100)  # 最后执行

        def before_consume(self, message, step):
            execution_order.append("third")

    broker = MemoryBroker()

    @step(
        from_broker=broker,
        middlewares=[
            ThirdMiddleware(),  # order=100
            FirstMiddleware(),  # order=10
            SecondMiddleware(),  # order=50
        ],
    )
    def handler(message):
        pass

    # 发送消息并启动消费
    broker.publish("test")
    step.start(block=False)
    time.sleep(0.5)  # 等待消息处理
    step.shutdown()

    # 验证执行顺序（应该按 order 排序）
    assert execution_order == ["first", "second", "third"], (
        f"中间件执行顺序错误：{execution_order}"
    )


def test_middleware_default_order():
    """测试默认 order 值为 100"""
    class DefaultOrderMiddleware(BaseMiddleware):
        def __init__(self):
            super().__init__()  # 默认 order=100

        def before_consume(self, message, step):
            pass

    middleware = DefaultOrderMiddleware()
    assert middleware.order == 100


def test_memory_broker_default_maxsize():
    """测试 MemoryBroker 默认 maxsize 为 1000"""
    from onestep.constants import DEFAULT_MEMORY_BROKER_MAXSIZE

    broker = MemoryBroker()

    assert broker.queue.maxsize == DEFAULT_MEMORY_BROKER_MAXSIZE, (
        f"默认 maxsize 应该是 {DEFAULT_MEMORY_BROKER_MAXSIZE}, "
        f"实际是 {broker.queue.maxsize}"
    )


def test_memory_broker_custom_maxsize():
    """测试可以自定义 maxsize"""
    custom_maxsize = 500

    broker = MemoryBroker(maxsize=custom_maxsize)

    assert broker.queue.maxsize == custom_maxsize


def test_memory_broker_zero_maxsize():
    """测试可以设置 maxsize=0（无限制）"""
    broker = MemoryBroker(maxsize=0)

    assert broker.queue.maxsize == 0


def test_base_consumer_default_timeout():
    """测试 BaseConsumer 默认 timeout 为 5000 毫秒"""
    from onestep.constants import DEFAULT_MEMORY_BROKER_TIMEOUT

    broker = MemoryBroker()
    consumer = broker.consume()

    assert consumer.timeout == DEFAULT_MEMORY_BROKER_TIMEOUT, (
        f"默认 timeout 应该是 {DEFAULT_MEMORY_BROKER_TIMEOUT} 毫秒，"
        f"实际是 {consumer.timeout} 毫秒"
    )


def test_base_consumer_custom_timeout():
    """测试可以自定义 timeout"""
    custom_timeout = 2000

    broker = MemoryBroker()
    consumer = broker.consume(timeout=custom_timeout)

    assert consumer.timeout == custom_timeout


def test_middleware_chain_execution():
    """测试中间件链执行顺序"""
    call_log = []

    class LoggingMiddleware(BaseMiddleware):
        def __init__(self, name, order=50):
            super().__init__(order=order)
            self.name = name

        def before_consume(self, message, step):
            call_log.append(f"before-{self.name}")

        def after_consume(self, message, step):
            call_log.append(f"after-{self.name}")

    broker = MemoryBroker()

    @step(
        from_broker=broker,
        middlewares=[
            LoggingMiddleware("first", order=10),
            LoggingMiddleware("second", order=20),
            LoggingMiddleware("third", order=30),
        ],
    )
    def handler(message):
        pass

    # 发送消息并启动消费
    broker.publish("test")
    step.start(block=False)
    time.sleep(0.5)  # 等待消息处理
    step.shutdown()

    # 验证中间件 before/after 调用顺序
    assert "before-first" in call_log
    assert "before-second" in call_log
    assert "before-third" in call_log
    assert "after-first" in call_log
    assert "after-second" in call_log
    assert "after-third" in call_log


def test_middleware_missing_signal_handler():
    """测试中间件没有实现某个信号处理器时的行为"""
    class PartialMiddleware(BaseMiddleware):
        def __init__(self):
            super().__init__(order=50)

        # 只实现 before_consume，不实现 after_consume
        def before_consume(self, message, step):
            pass

    @step(
        from_broker=MemoryBroker(),
        middlewares=[PartialMiddleware()],
    )
    def handler(message):
        pass

    # 不应该抛出异常
    pass
