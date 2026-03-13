from onestep import step, RedisPubSubBroker

broker = RedisPubSubBroker(channel="test")


@step(from_broker=broker)
def job_rds(message):
    print(message)


if __name__ == '__main__':
    step.set_debugging()
    step.start(block=True)
