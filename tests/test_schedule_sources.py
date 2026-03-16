import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from onestep import CronSource, IntervalSource


class FakeClock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def __call__(self) -> datetime:
        return self.current

    def advance(self, **kwargs) -> None:
        self.current += timedelta(**kwargs)


def test_interval_source_immediate_and_next_tick() -> None:
    async def scenario() -> None:
        tz = ZoneInfo("Asia/Shanghai")
        clock = FakeClock(datetime(2026, 3, 7, 10, 0, tzinfo=tz))
        source = IntervalSource.every(
            hours=1,
            immediate=True,
            payload={"job": "sync"},
            timezone="Asia/Shanghai",
            clock=clock,
        )
        await source.open()

        immediate = await source.fetch(1)
        assert len(immediate) == 1
        assert immediate[0].payload == {"job": "sync"}
        assert immediate[0].envelope.meta["scheduled_at"] == "2026-03-07T10:00:00+08:00"
        await immediate[0].ack()

        assert await source.fetch(1) == []
        clock.advance(minutes=59)
        assert await source.fetch(1) == []

        clock.advance(minutes=1)
        hourly = await source.fetch(1)
        assert len(hourly) == 1
        assert hourly[0].envelope.meta["scheduled_at"] == "2026-03-07T11:00:00+08:00"
        await hourly[0].ack()

    asyncio.run(scenario())


def test_interval_source_overlap_skip_drops_missed_ticks() -> None:
    async def scenario() -> None:
        tz = ZoneInfo("Asia/Shanghai")
        clock = FakeClock(datetime(2026, 3, 7, 10, 0, tzinfo=tz))
        source = IntervalSource.every(
            seconds=10,
            overlap="skip",
            timezone=tz,
            clock=clock,
        )
        await source.open()

        clock.advance(seconds=10)
        first = await source.fetch(1)
        assert len(first) == 1
        assert first[0].envelope.meta["scheduled_at"] == "2026-03-07T10:00:10+08:00"

        clock.advance(seconds=25)
        assert await source.fetch(1) == []

        await first[0].ack()
        assert await source.fetch(1) == []

        clock.advance(seconds=5)
        next_run = await source.fetch(1)
        assert len(next_run) == 1
        assert next_run[0].envelope.meta["scheduled_at"] == "2026-03-07T10:00:40+08:00"
        await next_run[0].ack()

    asyncio.run(scenario())


def test_interval_source_overlap_queue_serializes_missed_ticks() -> None:
    async def scenario() -> None:
        tz = ZoneInfo("Asia/Shanghai")
        clock = FakeClock(datetime(2026, 3, 7, 10, 0, tzinfo=tz))
        source = IntervalSource.every(
            seconds=10,
            overlap="queue",
            timezone=tz,
            clock=clock,
        )
        await source.open()

        clock.advance(seconds=10)
        first = await source.fetch(1)
        assert len(first) == 1
        assert first[0].envelope.meta["scheduled_at"] == "2026-03-07T10:00:10+08:00"

        clock.advance(seconds=25)
        assert await source.fetch(1) == []

        await first[0].ack()
        second = await source.fetch(1)
        assert len(second) == 1
        assert second[0].envelope.meta["scheduled_at"] == "2026-03-07T10:00:20+08:00"

        await second[0].ack()
        third = await source.fetch(1)
        assert len(third) == 1
        assert third[0].envelope.meta["scheduled_at"] == "2026-03-07T10:00:30+08:00"
        await third[0].ack()

    asyncio.run(scenario())


def test_cron_source_hourly_alias() -> None:
    async def scenario() -> None:
        tz = ZoneInfo("Asia/Shanghai")
        clock = FakeClock(datetime(2026, 3, 7, 10, 58, tzinfo=tz))
        source = CronSource(
            "@hourly",
            payload={"job": "hourly"},
            timezone="Asia/Shanghai",
            clock=clock,
        )
        await source.open()

        assert await source.fetch(1) == []
        clock.advance(minutes=2)

        first = await source.fetch(1)
        assert len(first) == 1
        assert first[0].payload == {"job": "hourly"}
        assert first[0].envelope.meta["scheduled_at"] == "2026-03-07T11:00:00+08:00"
        await first[0].ack()

        clock.advance(hours=1)
        second = await source.fetch(1)
        assert len(second) == 1
        assert second[0].envelope.meta["scheduled_at"] == "2026-03-07T12:00:00+08:00"
        await second[0].ack()

    asyncio.run(scenario())
