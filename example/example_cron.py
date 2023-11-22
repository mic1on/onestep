from onestep import step, CronBroker

once_broker = CronBroker("* * * * * */3", once=True)
cron_broker = CronBroker("* * * * * */3", body="hi cron")


@step(from_broker=[once_broker, cron_broker], workers=3)
def cron_task(message):
    print(message)
    return message


if __name__ == '__main__':
    step.set_debugging()
    step.start(block=True)
    # step.shutdown()
