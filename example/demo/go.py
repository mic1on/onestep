from onestep import step
from onestep.broker import MemoryBroker, WebHookBroker
from onestep.middleware import RedisConfigMiddleware
from client import rds_client

memory = MemoryBroker()
memory2 = MemoryBroker()
webhook = WebHookBroker(name="abc", path="/push")
webhook3 = WebHookBroker(path="/push2")
webhook2 = WebHookBroker(path="/push2", port=10010)


@step(to_broker=memory, middlewares=[
    RedisConfigMiddleware(rds_client, config_key="name")
])
def producer():
    yield from [f"www.baidu.com/{p}" for p in range(2)]


@step(from_broker=webhook, to_broker=memory2, workers=10)
async def consumer1(message):
    message.message = "处理了"
    return message


@step(from_broker=webhook2, to_broker=memory2, workers=10)
async def consumer1(message):
    message.message = "处理了2"
    return message


@step(from_broker=memory2, to_broker=memory, workers=10)
async def consumer2(message):
    message.message = "处理了2"
    return message


if __name__ == '__main__':
    producer()

    step.start(block=True)
