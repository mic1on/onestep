from onestep import step, CronBroker

once_broker = CronBroker("* * * * * */3", once=True)
cron_broker = CronBroker("* * * * * */3", body="hi cron")
# 使用自定义的开始时间（当前时间的前一天）
from datetime import datetime, timedelta

custom_start_time = datetime.now() - timedelta(days=1)
custom_broker = CronBroker("* * * * * */3", body="使用自定义时间", start_time=custom_start_time)

@step(from_broker=[once_broker, cron_broker, custom_broker], workers=3)
def cron_task(message):
    print(message)
    return message


if __name__ == '__main__':
    step.set_debugging()
    step.start(block=True)
    # step.shutdown()
