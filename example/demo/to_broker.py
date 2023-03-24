from addr import memory_addr
from onestep import onestep
import asyncio


@onestep(to_broker=memory_addr)
async def crawl_list():
    print("11111")
    return 1


@onestep(to_broker=memory_addr)
async def crawl_list2():
    print("2222")
    yield "2222"


@onestep(to_broker=memory_addr)
def crawl_list3():
    return "333333"


@onestep(to_broker=memory_addr)
def crawl_list4():
    yield from [f"www.baidu.com/{p}" for p in range(10)]


asyncio.run(crawl_list())
asyncio.run(crawl_list2())
crawl_list3()
crawl_list4()

print("consumer")
for message in memory_addr.consume():
    print(message)
