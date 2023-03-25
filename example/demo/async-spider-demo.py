import httpx

from onestep import step
from onestep.broker import MemoryBroker, RabbitMQBroker

list_broker = MemoryBroker()
result_broker = RabbitMQBroker("result", {"username": "admin", "password": "admin"})


@step(to_broker=list_broker)
def build_task():
    """模拟创建10个任务"""
    yield from range(1, 11)


@step(from_broker=list_broker, to_broker=result_broker, workers=10)
async def crawl_list(message):
    """模拟访问"""
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"https://httpbin.org/anything/{message.message}")
        url = resp.json().get("url")
        print("访问结果", url)
        return url


if __name__ == '__main__':
    build_task()
    step.start(block=True)
