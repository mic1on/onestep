from onestep import CronSource, OneStepApp

app = OneStepApp("cron-demo")


@app.task(source=CronSource("0 * * * *", timezone="Asia/Shanghai", overlap="skip"))
async def run_hourly(ctx, item):
    print("scheduled_at:", ctx.current.meta["scheduled_at"], "payload:", item)
