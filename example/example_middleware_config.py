from onestep import step, MemoryBroker
from onestep.middleware.config import RedisConsumeConfigMiddleware

from redis import Redis
from loguru import logger

rds = Redis(
    **{
        "host": "192.168.0.181",
        "port": 6379,
        "password": "password",
        "db": 0
    }
)


class MyConfigMiddleware(RedisConsumeConfigMiddleware):
    def process(self, step, message, *args, **kwargs):
        if message.body.get("site_name"):
            return self.get(message.body.get("site_name"))


# def
config_group = "onestep:config:fang.crawl"
config_middleware = MyConfigMiddleware(client=rds, config_group=config_group)
todo_broker = MemoryBroker()

# mock
cfg = '{ "domain": "nj", "region": { "areaId": null, "cityId": 659004882, "provinceId": 320000 }}'
rds.hset(config_group, "南京", cfg)
for i in range(3):
    todo_broker.send({"site_name": "南京", "say": f"hello world {i}"})


# 由于from_broker和to_broker都是todo_broker，
# 所以这个step会在todo_broker中循环执行
@step(
    from_broker=todo_broker,
    to_broker=todo_broker,
    workers=1,
    middlewares=[config_middleware])
def do_something(todo):
    logger.info(f"{todo=} {todo.config=}")


if __name__ == '__main__':
    # step.set_debugging()

    step.start(block=True)
