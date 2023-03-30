from onestep import step, CronBroker


@step(from_broker=CronBroker("* * * * * */3", a=1))
def cron_task(message):
    print(message)
    return message


if __name__ == '__main__':
    step.start(block=True)
