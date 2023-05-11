from onestep import step
from onestep.broker import WebHookBroker

step.set_debugging()

# 对外提供一个webhook接口，接收外部的消息
webhook_broker = WebHookBroker(path="/push")


@step(from_broker=webhook_broker)
def waiting_messages(message):
    print("收到消息：", message)
    message.requeue()


if __name__ == '__main__':
    step.set_debugging()
    step.start(block=True)
