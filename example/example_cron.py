from onestep import step, CronBroker

a = CronBroker("* * * * * */3", a=1)


@step(from_broker=a, workers=3)
def cron_task(message):
    print(message)
    return message


if __name__ == '__main__':
    step.set_debugging()
    step.start()
    step.shutdown()
