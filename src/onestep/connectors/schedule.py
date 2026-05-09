from __future__ import annotations

import asyncio
import copy
import heapq
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any, Callable
from zoneinfo import ZoneInfo

from onestep.envelope import Envelope

from .base import Delivery, Source

MONTH_NAMES = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}

DOW_NAMES = {
    "sun": 0,
    "mon": 1,
    "tue": 2,
    "wed": 3,
    "thu": 4,
    "fri": 5,
    "sat": 6,
}

CRON_ALIASES = {
    "@hourly": "0 * * * *",
    "@daily": "0 0 * * *",
    "@weekly": "0 0 * * 0",
    "@monthly": "0 0 1 * *",
    "@yearly": "0 0 1 1 *",
}


class ScheduleDelivery(Delivery):
    def __init__(self, source: "BaseScheduleSource", envelope: Envelope) -> None:
        super().__init__(envelope)
        self._source = source

    async def ack(self) -> None:
        await self._source.finish_success()

    async def retry(self, *, delay_s: float | None = None) -> None:
        await self._source.finish_retry(self.envelope, delay_s=delay_s)

    async def fail(self, exc: Exception | None = None) -> None:
        await self._source.finish_failure()


class BaseScheduleSource(Source):
    supports_manual_run = True

    def __init__(
        self,
        name: str,
        *,
        payload: Any = None,
        immediate: bool = False,
        overlap: str = "allow",
        timezone: str | tzinfo | None = None,
        timezone_name: str | tzinfo | None = None,
        poll_interval_s: float = 1.0,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__(name)
        if overlap not in {"allow", "skip", "queue"}:
            raise ValueError("overlap must be one of: allow, skip, queue")
        self.payload = payload
        self.immediate = immediate
        self.overlap = overlap
        resolved_timezone = timezone if timezone is not None else timezone_name
        self.timezone = resolve_timezone(resolved_timezone)
        self.poll_interval_s = max(0.01, poll_interval_s)
        self._clock = clock or self._default_now
        self._initialized = False
        self._pending_immediate = immediate
        self._next_fire_at: datetime | None = None
        self._inflight = 0
        self._sequence = 0
        self._queued_runs: deque[datetime] = deque()
        self._retry_heap: list[tuple[datetime, int, Envelope]] = []
        self._retry_sequence = 0
        self._lock: asyncio.Lock | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    async def open(self) -> None:
        lock = self._runtime_lock()
        async with lock:
            if not self._initialized:
                self._initialize_locked(self._now())

    async def close(self) -> None:
        return None

    async def fetch(self, limit: int) -> list[Delivery]:
        await self.open()
        fetch_limit = max(1, limit)
        lock = self._runtime_lock()
        async with lock:
            now = self._now()
            if not self._initialized:
                self._initialize_locked(now)

            deliveries = self._drain_retry_locked(now, fetch_limit)
            if deliveries:
                return deliveries

            if self._pending_immediate:
                self._pending_immediate = False
                immediate_time = now
                immediate_deliveries = self._handle_due_runs_locked([immediate_time], fetch_limit)
                if immediate_deliveries:
                    return immediate_deliveries

            if self.overlap == "queue" and self._inflight == 0 and self._queued_runs:
                return [self._make_delivery_locked(self._queued_runs.popleft())]

            due_runs = self._collect_due_runs_locked(now)
            if not due_runs:
                return []
            return self._handle_due_runs_locked(due_runs, fetch_limit)

    async def finish_success(self) -> None:
        async with self._runtime_lock():
            self._inflight = max(0, self._inflight - 1)

    async def finish_failure(self) -> None:
        async with self._runtime_lock():
            self._inflight = max(0, self._inflight - 1)

    async def finish_retry(self, envelope: Envelope, *, delay_s: float | None = None) -> None:
        ready_at = self._now() + timedelta(seconds=max(delay_s or 0, 0))
        retry_envelope = Envelope(
            body=copy.deepcopy(envelope.body),
            meta=dict(envelope.meta),
            attempts=envelope.attempts + 1,
        )
        async with self._runtime_lock():
            self._inflight = max(0, self._inflight - 1)
            self._retry_sequence += 1
            heapq.heappush(self._retry_heap, (ready_at, self._retry_sequence, retry_envelope))

    def _runtime_lock(self) -> asyncio.Lock:
        current_loop = asyncio.get_running_loop()
        if self._lock is None or self._loop is not current_loop:
            self._lock = asyncio.Lock()
            self._loop = current_loop
            self._initialized = False
            self._pending_immediate = self.immediate
            self._next_fire_at = None
            self._inflight = 0
            self._sequence = 0
            self._queued_runs = deque()
            self._retry_heap = []
            self._retry_sequence = 0
        return self._lock

    def _default_now(self) -> datetime:
        return datetime.now(self.timezone)

    def _now(self) -> datetime:
        current = self._clock()
        if current.tzinfo is None:
            return current.replace(tzinfo=self.timezone)
        return current.astimezone(self.timezone)

    def _initialize_locked(self, now: datetime) -> None:
        self._next_fire_at = self._initial_fire_at(now)
        self._initialized = True

    def _drain_retry_locked(self, now: datetime, limit: int) -> list[Delivery]:
        deliveries: list[Delivery] = []
        while self._retry_heap and len(deliveries) < limit:
            ready_at, _, envelope = self._retry_heap[0]
            if ready_at > now:
                break
            heapq.heappop(self._retry_heap)
            self._inflight += 1
            deliveries.append(ScheduleDelivery(self, envelope))
        return deliveries

    def _collect_due_runs_locked(self, now: datetime, max_due: int = 1000) -> list[datetime]:
        if self._next_fire_at is None:
            return []
        due_runs: list[datetime] = []
        while self._next_fire_at is not None and self._next_fire_at <= now and len(due_runs) < max_due:
            due_runs.append(self._next_fire_at)
            self._next_fire_at = self._next_fire_after(self._next_fire_at)
        return due_runs

    def _handle_due_runs_locked(self, due_runs: list[datetime], limit: int) -> list[Delivery]:
        if not due_runs or limit < 1:
            return []

        if self.overlap == "allow":
            deliveries: list[Delivery] = []
            for scheduled_at in due_runs[:limit]:
                deliveries.append(self._make_delivery_locked(scheduled_at))
            return deliveries

        if self.overlap == "skip":
            if self._inflight > 0:
                return []
            return [self._make_delivery_locked(due_runs[-1])]

        if self._inflight > 0:
            self._queued_runs.extend(due_runs)
            return []

        delivery = self._make_delivery_locked(due_runs[0])
        if len(due_runs) > 1:
            self._queued_runs.extend(due_runs[1:])
        return [delivery]

    def _make_delivery_locked(self, scheduled_at: datetime) -> ScheduleDelivery:
        body = self._build_payload()
        envelope = Envelope(
            body=body,
            meta={
                "source": self.name,
                "scheduled_at": scheduled_at.isoformat(),
                "sequence": self._sequence,
            },
            attempts=0,
        )
        self._sequence += 1
        self._inflight += 1
        return ScheduleDelivery(self, envelope)

    def _build_payload(self) -> Any:
        if callable(self.payload):
            return self.payload()
        return copy.deepcopy(self.payload)

    def _initial_fire_at(self, now: datetime) -> datetime:
        raise NotImplementedError

    def _next_fire_after(self, current_fire_at: datetime) -> datetime:
        raise NotImplementedError


class IntervalSource(BaseScheduleSource):
    def __init__(
        self,
        interval: timedelta,
        *,
        payload: Any = None,
        immediate: bool = False,
        overlap: str = "allow",
        timezone: str | tzinfo | None = None,
        timezone_name: str | tzinfo | None = None,
        poll_interval_s: float | None = None,
        clock: Callable[[], datetime] | None = None,
        name: str | None = None,
    ) -> None:
        if interval.total_seconds() <= 0:
            raise ValueError("interval must be greater than zero")
        default_poll = min(max(interval.total_seconds() / 10.0, 0.01), 1.0)
        super().__init__(
            name or f"interval:{int(interval.total_seconds())}s",
            payload=payload,
            immediate=immediate,
            overlap=overlap,
            timezone=timezone,
            timezone_name=timezone_name,
            poll_interval_s=default_poll if poll_interval_s is None else poll_interval_s,
            clock=clock,
        )
        self.interval = interval

    @classmethod
    def every(
        cls,
        *,
        days: int = 0,
        hours: int = 0,
        minutes: int = 0,
        seconds: int = 0,
        payload: Any = None,
        immediate: bool = False,
        overlap: str = "allow",
        timezone: str | tzinfo | None = None,
        timezone_name: str | tzinfo | None = None,
        poll_interval_s: float | None = None,
        clock: Callable[[], datetime] | None = None,
        name: str | None = None,
    ) -> "IntervalSource":
        interval = timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)
        return cls(
            interval,
            payload=payload,
            immediate=immediate,
            overlap=overlap,
            timezone=timezone,
            timezone_name=timezone_name,
            poll_interval_s=poll_interval_s,
            clock=clock,
            name=name,
        )

    def _initial_fire_at(self, now: datetime) -> datetime:
        return now + self.interval

    def _next_fire_after(self, current_fire_at: datetime) -> datetime:
        return current_fire_at + self.interval


class CronField:
    def __init__(
        self,
        expression: str,
        *,
        minimum: int,
        maximum: int,
        names: dict[str, int] | None = None,
        sunday_as_zero: bool = False,
    ) -> None:
        self.expression = expression
        self.any = expression.strip() == "*"
        self.minimum = minimum
        self.maximum = maximum
        self.names = names or {}
        self.sunday_as_zero = sunday_as_zero
        self.values = self._parse(expression)

    def matches(self, value: int) -> bool:
        return value in self.values

    def _parse(self, expression: str) -> set[int]:
        values: set[int] = set()
        for chunk in expression.split(","):
            values.update(self._parse_chunk(chunk.strip()))
        return values

    def _parse_chunk(self, chunk: str) -> set[int]:
        if not chunk:
            raise ValueError("empty cron field chunk")
        if chunk == "*":
            return set(range(self.minimum, self.maximum + 1))

        if "/" in chunk:
            base, step_text = chunk.split("/", 1)
            step = int(step_text)
            if step <= 0:
                raise ValueError("cron step must be > 0")
            if base == "*":
                start = self.minimum
                end = self.maximum
            elif "-" in base:
                start_text, end_text = base.split("-", 1)
                start = self._parse_value(start_text)
                end = self._parse_value(end_text)
            else:
                start = self._parse_value(base)
                end = self.maximum
            if start > end:
                raise ValueError(f"invalid cron range: {chunk}")
            return set(range(start, end + 1, step))

        if "-" in chunk:
            start_text, end_text = chunk.split("-", 1)
            start = self._parse_value(start_text)
            end = self._parse_value(end_text)
            if start > end:
                raise ValueError(f"invalid cron range: {chunk}")
            return set(range(start, end + 1))

        return {self._parse_value(chunk)}

    def _parse_value(self, value: str) -> int:
        lowered = value.lower()
        if lowered in self.names:
            parsed = self.names[lowered]
        else:
            parsed = int(value)
        if self.sunday_as_zero and parsed == 7:
            parsed = 0
        if parsed < self.minimum or parsed > self.maximum:
            raise ValueError(f"cron value {value!r} is outside {self.minimum}-{self.maximum}")
        return parsed


class CronSchedule:
    def __init__(
        self,
        expression: str,
        *,
        timezone: str | tzinfo | None = None,
        timezone_name: str | tzinfo | None = None,
    ) -> None:
        normalized = CRON_ALIASES.get(expression.strip().lower(), expression.strip())
        fields = normalized.split()
        if len(fields) != 5:
            raise ValueError("CronSource only supports standard 5-field cron expressions")
        self.expression = normalized
        resolved_timezone = timezone if timezone is not None else timezone_name
        self.timezone = resolve_timezone(resolved_timezone)
        self.minute = CronField(fields[0], minimum=0, maximum=59)
        self.hour = CronField(fields[1], minimum=0, maximum=23)
        self.day_of_month = CronField(fields[2], minimum=1, maximum=31)
        self.month = CronField(fields[3], minimum=1, maximum=12, names=MONTH_NAMES)
        self.day_of_week = CronField(
            fields[4],
            minimum=0,
            maximum=6,
            names=DOW_NAMES,
            sunday_as_zero=True,
        )

    def matches(self, dt: datetime) -> bool:
        current = dt.astimezone(self.timezone)
        if not self.minute.matches(current.minute):
            return False
        if not self.hour.matches(current.hour):
            return False
        if not self.month.matches(current.month):
            return False

        dom_match = self.day_of_month.matches(current.day)
        cron_weekday = (current.weekday() + 1) % 7
        dow_match = self.day_of_week.matches(cron_weekday)

        if self.day_of_month.any and self.day_of_week.any:
            return True
        if self.day_of_month.any:
            return dow_match
        if self.day_of_week.any:
            return dom_match
        return dom_match or dow_match

    def first_fire_at(self, now: datetime) -> datetime:
        current = now.astimezone(self.timezone)
        on_minute = current.second == 0 and current.microsecond == 0
        if on_minute and self.matches(current):
            return current
        return self.next_after(current)

    def next_after(self, current: datetime) -> datetime:
        candidate = current.astimezone(self.timezone).replace(second=0, microsecond=0) + timedelta(minutes=1)
        for _ in range(366 * 24 * 60 * 2):
            if self.matches(candidate):
                return candidate
            candidate += timedelta(minutes=1)
        raise RuntimeError(f"unable to find next fire time for cron expression {self.expression!r}")


class CronSource(BaseScheduleSource):
    def __init__(
        self,
        expression: str,
        *,
        payload: Any = None,
        immediate: bool = False,
        overlap: str = "allow",
        timezone: str | tzinfo | None = None,
        timezone_name: str | tzinfo | None = None,
        poll_interval_s: float = 1.0,
        clock: Callable[[], datetime] | None = None,
        name: str | None = None,
    ) -> None:
        self.schedule = CronSchedule(
            expression,
            timezone=timezone,
            timezone_name=timezone_name,
        )
        super().__init__(
            name or f"cron:{self.schedule.expression}",
            payload=payload,
            immediate=immediate,
            overlap=overlap,
            timezone=self.schedule.timezone,
            timezone_name=self.schedule.timezone,
            poll_interval_s=poll_interval_s,
            clock=clock,
        )

    def _initial_fire_at(self, now: datetime) -> datetime:
        return self.schedule.first_fire_at(now)

    def _next_fire_after(self, current_fire_at: datetime) -> datetime:
        return self.schedule.next_after(current_fire_at)


def resolve_timezone(value: str | tzinfo | None) -> tzinfo:
    if isinstance(value, tzinfo):
        return value
    if isinstance(value, str):
        return ZoneInfo(value)
    return datetime.now().astimezone().tzinfo or timezone.utc


__all__ = [
    "CronSource",
    "IntervalSource",
    "ScheduleDelivery",
]
