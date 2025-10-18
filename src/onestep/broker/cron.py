"""
使用 CRON 表达式触发任务执行

支持人性化 DSL 与宏：
- DSL：Cron.every(minutes=5)、Cron.daily(at="09:00")、Cron.weekly(on=["mon","fri"], at="10:30") 等
- 宏：@hourly/@daily/@weekly/@monthly/@yearly 以及扩展 @workdays/@weekends/@every 5m/2h/3d/1mo
"""
import logging
import threading
from datetime import datetime
from typing import Any

from croniter import croniter
from ..cron import resolve_cron

from .memory import MemoryBroker, MemoryConsumer

logger = logging.getLogger(__name__)


class CronBroker(MemoryBroker):

    def __init__(self, cron, name=None, middlewares=None, body: Any = None, start_time=None, *args, **kwargs):
        super().__init__(name=name, middlewares=middlewares, *args, **kwargs)
        # 支持 DSL/宏/原始字符串：统一解析为标准表达式
        self.cron = resolve_cron(cron)
        self.start_time = start_time or datetime.now()
        self.itr = croniter(self.cron, self.start_time)
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
