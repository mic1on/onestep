import time

from onestep import step
from onestep.broker import RedisStreamBroker, MemoryBroker

step.set_debugging()

# 配置队列及连接信息
rds_broker = RedisStreamBroker(stream="onestep_demo", params={})

# 模拟一个内存队列
todo_broker = MemoryBroker()


rds_broker.send("1hello world")
rds_broker.send({"2hello": "world"})

pass
# todo_broker.send("1")


@step(from_broker=rds_broker, to_broker=todo_broker, workers=1)
def build_todo_list(message):
    print("build_todo_list", message.body)
    return message


if __name__ == '__main__':
    step.start()
    time.sleep(1)
    step.shutdown()
