from onestep import step, CronBroker
from onestep.worker import ThreadPoolWorker

cron_broker = CronBroker("* * * * * */3", body="hi cron")


@step(from_broker=cron_broker,
      workers=3,
      worker_class=ThreadPoolWorker)
def cron_task(message):
    print(message)
    return message


if __name__ == '__main__':
    step.set_debugging()
    step.start(block=True)
    # step.shutdown()
