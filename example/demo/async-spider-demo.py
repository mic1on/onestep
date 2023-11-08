import httpx

from onestep import step
from onestep.broker import MemoryBroker, RabbitMQBroker
from onestep.middleware import MemoryUniqueMiddleware

page_broker = MemoryBroker()
list_broker = RabbitMQBroker("spider.list", {"username": "admin", "password": "admin"})
detail_broker = RabbitMQBroker("spider.detail", {"username": "admin", "password": "admin"})


@step(to_broker=page_broker)
def build_task():
    """模拟创建10个任务"""
    yield from range(1, 11)


@step(from_broker=page_broker, to_broker=list_broker, workers=2)
async def crawl_list(message):
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"https://httpbin.org/anything/{message.body}", timeout=None)
        url = resp.json().get("url")
        return url


@step(from_broker=list_broker, to_broker=detail_broker,
      workers=10, middlewares=[MemoryUniqueMiddleware()])
async def crawl_detail(message):
    async with httpx.AsyncClient() as client:
        resp = await client.get(message.body, timeout=None)
        return resp.json()


if __name__ == '__main__':
    build_task()
    step.set_debugging()
    step.start(block=True)
