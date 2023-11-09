import time

from onestep import step
from onestep.broker import SQSBroker, MemoryBroker

step.set_debugging()

# 配置RabbitMQ队列及连接信息
sqs_broker = SQSBroker(
    "job.fifo",
    {
        "region_name": "cn-northwest-1",
        "aws_access_key_id": "AKIASCW2G2WLBXLUKS3T",
        "aws_secret_access_key": "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"
    },
    queue_params={
        "MaximumMessageSize": str(4096),
        "ReceiveMessageWaitTimeSeconds": str(10),
        "VisibilityTimeout": str(300),
        "FifoQueue": str(True),
        "ContentBasedDeduplication": str(True),
    },
    message_group_id="test"
)

# 模拟一个内存队列
todo_broker = MemoryBroker()


@step(from_broker=sqs_broker, to_broker=todo_broker, workers=3)
def build_todo_list(message):
    print("build_todo_list", message.body)
    # 返回的内容将发给RabbitMQ队列
    return message


if __name__ == '__main__':
    step.start(block=True)
    time.sleep(2)
    step.shutdown()
