"""
使用CRON表达式触发任务执行
"""
import logging
import threading
from datetime import datetime
from typing import Any

from croniter import croniter

from .base import BaseLocalBroker, BaseLocalConsumer

logger = logging.getLogger(__name__)


class CronBroker(BaseLocalBroker):
    _thread = None

    def __init__(self, cron, name=None, middlewares=None, body: Any = None, *args, **kwargs):
        super().__init__(name=name, middlewares=middlewares, *args, **kwargs)
        self.cron = cron
        self.itr = croniter(cron, datetime.now())
        self.next_fire_time = self.itr.get_next(datetime)
        self.body = body

    def _scheduler(self):
        if self.next_fire_time <= datetime.now():
            self.next_fire_time = self.itr.get_next(datetime)
            self.publish(self.body)

        self._thread = threading.Timer(interval=1, function=self._scheduler)
        self._thread.start()

    def consume(self):
        self._scheduler()
        return CronConsumer(self.queue)

    def shutdown(self):
        self._thread.cancel()


class CronConsumer(BaseLocalConsumer):
    ...
