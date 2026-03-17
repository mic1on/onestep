---
title: 入门教程 | 指南
---

# 入门教程

### 定时任务

这里将演示一个最简单的场景，我们需要定时执行任务，然后将数据保存到队列中。

```python
from onestep import step
from onestep.broker import CronBroker, RabbitMQBroker

# 定义任务的输入来自定时任务
cron_broker = CronBroker("* * * * * */3")
# 定义任务的结果输出至队列
result_broker = RabbitMQBroker("result")

# 使用装饰器定义任务
@step(from_broker=cron_broker, to_broker=result_broker)
def cron_job(message):
    # to do something
    return random.randint(1, 100)

# 启动任务
step.start(block=True)
```

启动任务后，`cron_job`函数将会每3秒执行一次，并且将结果保存到队列中。


### 爬虫

在爬虫中，抓取列表页，然后进入详情页，再抓取详情页中的数据是一个十分常见的场景。

```python
from onestep import step
from onestep.broker import MemoryBroker, RabbitMQBroker
from onestep.middleware import MemoryUniqueMiddleware

# 定义任务的数据持久化
page_broker = MemoryBroker()
list_broker = RabbitMQBroker("spider.list")
detail_broker = RabbitMQBroker("spider.detail")

# 定义列表抓取，使用10线程来抓取，将抓取的url交给详情
@step(from_broker=page_broker, to_broker=list_broker, workers=2)
async def crawl_list(message):
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"https://httpbin.org/anything/{message.body}")
        url = resp.json().get("url")
        return url


# 定义详情抓取，将抓取的结果保存到队列。
@step(from_broker=list_broker, to_broker=detail_broker,
      middlewares=[MemoryUniqueMiddleware()])
async def crawl_detail(message):
    async with httpx.AsyncClient() as client:
        resp = await client.get(message.body)
        return resp.json()

if __name__ == '__main__':
    step.set_debugging()
    step.start(block=True)
```

我们模拟是内存page_broker来生成任务的，所以我们需要模拟投递10个页面。有两种方式：

```python
@step(to_broker=page_broker)
def build_task():
    """模拟创建10个任务"""
    yield from range(1, 11)
```

或者，直接对broker进行消息publish
```python
for i in range(1, 11):
    page_broker.publish(i)
```

这样，`crawl_list`收到页码负责抓列表，它采用2个线程并发抓取，抓取到url后，将url交给`crawl_detail`，`crawl_detail`收到后访问详情，将结果保存到队列。

值得注意的是：`crawl_detail`使用了`MemoryUniqueMiddleware`，它是一个中间件，用于保证队列中不重复。这是一个基于本地内存的去重，如果有更高的需求可以采用redis或者布隆过滤器等等...

有了MQ的加持，`crawl_list`和`crawl_detail`可以在任何能连接MQ的设备上分布式运行，你完全可以在A机器上运行`crawl_list`，在B机器上运行`crawl_detail`。
