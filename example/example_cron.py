from datetime import datetime, timedelta
from onestep import step, CronBroker, Cron

once_broker = CronBroker("* * * * * */3", once=True, body="仅仅执行一次")
# 使用自定义的开始时间（当前时间的前一天）
custom_start_time = datetime.now() - timedelta(days=1)
custom_broker = CronBroker(
    "* * * * * */3", body="使用自定义时间", start_time=custom_start_time
)

# 新增：DSL 与宏示例
dsl_every_30s = CronBroker(Cron.every(seconds=30), body="DSL 每 30 秒")
dsl_weekly = CronBroker(
    Cron.weekly(on=["mon", "fri"], at="10:30"), body="DSL 每周一/周五 10:30"
)
macro_workdays = CronBroker("@workdays", body="宏：工作日 00:00")
macro_every_1m = CronBroker("@every 1m", body="宏：每 1 分钟")


@step(
    from_broker=[once_broker, dsl_every_30s, dsl_weekly, macro_workdays, macro_every_1m]
)
def cron_task(message):
    print(message)
    return


if __name__ == "__main__":
    step.set_debugging()
    step.start(block=True)
    # step.shutdown()
