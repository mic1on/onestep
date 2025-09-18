"""
使用CRON表达式触发任务执行
"""
import logging
import threading
from datetime import datetime
from typing import Any

from croniter import croniter

from .memory import MemoryBroker, MemoryConsumer

logger = logging.getLogger(__name__)


class CronBroker(MemoryBroker):

    def __init__(self, cron, name=None, middlewares=None, body: Any = None, start_time=None, *args, **kwargs):
        super().__init__(name=name, middlewares=middlewares, *args, **kwargs)
        self.cron = cron
        self.start_time = start_time or datetime.now()
        self.itr = croniter(cron, self.start_time)
        self.next_fire_time = self.itr.get_next(datetime)
        self.body = body
        self._thread = None

    def _scheduler(self):
        if self.next_fire_time <= datetime.now():
            self.next_fire_time = self.itr.get_next(datetime)
            self.publish(self.body)

        self._thread = threading.Timer(interval=1, function=self._scheduler)
        self._thread.start()

    def consume(self, *args, **kwargs):
        self._scheduler()
        return CronConsumer(self, *args, **kwargs)

    def shutdown(self):
        if self._thread:
            self._thread.cancel()


class CronConsumer(MemoryConsumer):
    ...
