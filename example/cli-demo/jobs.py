from onestep import step, CronBroker

cron_broker = CronBroker("* * * * * */3", body="hi cron")


@step(from_broker=cron_broker)
def cron_job(message):
    print(message)
