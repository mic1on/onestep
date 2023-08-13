"""
使用CRON表达式触发任务执行
"""
import logging
import threading
from datetime import datetime

from croniter import croniter

from .base import BaseLocalBroker, BaseLocalConsumer

logger = logging.getLogger(__name__)


class CronBroker(BaseLocalBroker):

    def __init__(self, cron, name=None, middlewares=None, **kwargs):
        super().__init__(name=name, middlewares=middlewares)
        self.cron = cron
        self.itr = croniter(cron, datetime.now())
        self.next_fire_time = self.itr.get_next(datetime)
        self.kwargs = kwargs
        self._scheduler()

    def _scheduler(self):
        if self.next_fire_time <= datetime.now():
            self.next_fire_time = self.itr.get_next(datetime)
            self.publish(self.kwargs)

        threading.Timer(interval=1, function=self._scheduler).start()

    def consume(self):
        return CronConsumer(self.queue)


class CronConsumer(BaseLocalConsumer):
    ...
