# -*- coding: utf-8 -*-
"""
Cron DSL/Builder 与宏解析

提供人性化的 API 来生成 cron 表达式，并支持常用别名/宏：
- Cron.every(minutes=5) / Cron.every(seconds=10) / Cron.every(hours=2) / Cron.every(days=3) / Cron.every(months=1)
- Cron.daily(at="09:00")
- Cron.weekly(on="mon" or ["mon","fri"], at="10:30")
- Cron.monthly(on_day=1, at="00:00")
- Cron.yearly(on="01-01", at="00:00")
- 宏别名：@hourly/@daily/@weekly/@monthly/@yearly
- 扩展宏：@workdays（1-5）/@weekends（0,6）/@every <n><unit>（unit: s/m/h/d/mo）

注意：
- 标准 5 字段：minute hour day month day_of_week
- 当包含 seconds（如 `at` 使用 HH:MM:SS 或 `every(seconds=...)`）时输出 6 字段：second minute hour day month day_of_week
- `@every <n>d` 等按“日/月字段取模”的语义生成表达式，并非滚动间隔。
"""
from __future__ import annotations

from typing import List, Optional, Tuple, Union

DOW_MAP = {
    "sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6,
}

ALIAS_MAP = {
    "@hourly": "0 * * * *",
    "@daily": "0 0 * * *",
    "@weekly": "0 0 * * 0",
    "@monthly": "0 0 1 * *",
    "@yearly": "0 0 1 1 *",
}


def _parse_at(at: Optional[str]) -> Tuple[Optional[int], int, int]:
    """解析 at 参数，支持 HH:MM 或 HH:MM:SS。
    返回 (second, minute, hour)。如果不传 at，默认 minute=0, hour=0。
    """
    if not at:
        return None, 0, 0
    parts = at.strip().split(":")
    if len(parts) == 2:
        h, m = parts
        return None, int(m), int(h)
    elif len(parts) == 3:
        h, m, s = parts
        return int(s), int(m), int(h)
    raise ValueError(f"Invalid time format for at='{at}', expected HH:MM or HH:MM:SS")


def _to_dow_expr(on: Union[str, int, List[Union[str, int]]]) -> str:
    """将星期输入转换为 day_of_week 字段表达式。
    支持：字符串缩写（mon...sun）、数字 0-6、列表。
    """
    def _to_num(v: Union[str, int]) -> int:
        if isinstance(v, int):
            if v < 0 or v > 6:
                raise ValueError("day_of_week must be in 0-6")
            return v
        v = str(v).lower()
        if v.isdigit():
            num = int(v)
            if num < 0 or num > 6:
                raise ValueError("day_of_week must be in 0-6")
            return num
        if v not in DOW_MAP:
            raise ValueError(f"Unknown day_of_week '{v}'")
        return DOW_MAP[v]

    if isinstance(on, (list, tuple, set)):
        vals = sorted({_to_num(v) for v in on})
        return ",".join(str(x) for x in vals)
    else:
        return str(_to_num(on))


def _join_fields(second: Optional[int], minute: Union[int, str], hour: Union[int, str], day: Union[int, str], month: Union[int, str], dow: Union[int, str]) -> str:
    """组合 5/6 字段。若 second 为 None，输出 5 字段，否则输出 6 字段（秒在最后）。"""
    if second is None:
        return f"{minute} {hour} {day} {month} {dow}"
    return f"{minute} {hour} {day} {month} {dow} {second}"


class Cron:
    @staticmethod
    def every(*, seconds: Optional[int] = None, minutes: Optional[int] = None, hours: Optional[int] = None, days: Optional[int] = None, months: Optional[int] = None) -> str:
        """每隔指定单位执行。
        只能指定一个非 None 的单位。
        - seconds: 6 字段：*/s * * * * *
        - minutes: 5 字段：*/m * * * *
        - hours:   5 字段：0 */h * * *
        - days:    5 字段：0 0 */d * *
        - months:  5 字段：0 0 1 */mo *
        """
        units = [("seconds", seconds), ("minutes", minutes), ("hours", hours), ("days", days), ("months", months)]
        chosen = [(k, v) for k, v in units if v is not None]
        if len(chosen) != 1:
            raise ValueError("Cron.every() must specify exactly one unit")
        k, v = chosen[0]
        if not isinstance(v, int) or v <= 0:
            raise ValueError("Interval must be a positive integer")
        if k == "seconds":
            return f"* * * * * */{v}"
        if k == "minutes":
            return "*/%d * * * *" % v
        if k == "hours":
            return "0 */%d * * *" % v
        if k == "days":
            return "0 0 */%d * *" % v
        if k == "months":
            return "0 0 1 */%d *" % v
        raise RuntimeError("unreachable")

    @staticmethod
    def daily(*, at: Optional[str] = None) -> str:
        s, m, h = _parse_at(at)
        day, month, dow = "*", "*", "*"
        return _join_fields(s, m, h, day, month, dow)

    @staticmethod
    def weekly(*, on: Union[str, int, List[Union[str, int]]] = "mon", at: Optional[str] = None) -> str:
        s, m, h = _parse_at(at)
        dow = _to_dow_expr(on)
        day, month = "*", "*"
        return _join_fields(s, m, h, day, month, dow)

    @staticmethod
    def monthly(*, on_day: Union[int, List[int]] = 1, at: Optional[str] = None) -> str:
        s, m, h = _parse_at(at)
        month, dow = "*", "*"
        def _to_day(v: Union[int, str]) -> int:
            iv = int(v)
            if iv < 1 or iv > 31:
                raise ValueError("day_of_month must be in 1-31")
            return iv
        if isinstance(on_day, (list, tuple, set)):
            day = ",".join(str(_to_day(v)) for v in sorted({int(x) for x in on_day}))
        else:
            day = str(_to_day(on_day))
        return _join_fields(s, m, h, day, month, dow)

    @staticmethod
    def yearly(*, on: str = "01-01", at: Optional[str] = None) -> str:
        s, m, h = _parse_at(at)
        dow = "*"
        try:
            mon_str, day_str = on.split("-")
            month = str(int(mon_str))
            day = str(int(day_str))
        except Exception:
            raise ValueError("yearly(on=...) expects 'MM-DD', e.g., '01-01'")
        return _join_fields(s, m, h, day, month, dow)

    @staticmethod
    def alias(name: str) -> str:
        """返回内置宏别名对应的表达式（如果已知）。未知别名原样返回，以便 croniter 自行处理。"""
        return ALIAS_MAP.get(name.strip().lower(), name)


def resolve_cron(cron_like: str) -> str:
    """将类似 cron 的输入（字符串/宏）解析为标准 cron 表达式。
    - 内置关键字直接映射或保留（croniter 也支持）；
    - 扩展宏：@workdays/@weekends/@every <n><unit>（unit: s/m/h/d/mo）。
    """
    s = str(cron_like).strip()
    if not s.startswith("@"):
        return s
    lower = s.lower()
    # 内置别名
    if lower in ALIAS_MAP:
        return ALIAS_MAP[lower]
    # 扩展：工作日/周末（默认 00:00 执行）
    if lower == "@workdays":
        return "0 0 * * 1-5"
    if lower == "@weekends":
        return "0 0 * * 0,6"
    # 扩展：@every <n><unit>
    if lower.startswith("@every"):
        tail = lower.replace("@every", "", 1).strip()
        if not tail:
            raise ValueError("@every requires an interval, e.g., '@every 5m'")
        # 支持类似 "5m" / "10 h" / "2d" / "3mo" / "30s"
        tail = tail.replace(" ", "")
        # 处理 'mo' 与单位优先级
        if tail.endswith("mo"):
            num = int(tail[:-2])
            if num <= 0:
                raise ValueError("@every months must be > 0")
            return "0 0 1 */%d *" % num
        unit = tail[-1]
        try:
            num = int(tail[:-1])
        except Exception:
            raise ValueError("@every expects '<n><unit>', units: s/m/h/d/mo")
        if num <= 0:
            raise ValueError("@every interval must be > 0")
        if unit == "s":
            return "* * * * * */%d" % num
        if unit == "m":
            return "*/%d * * * *" % num
        if unit == "h":
            return "0 */%d * * *" % num
        if unit == "d":
            return "0 0 */%d * *" % num
        if unit == "w":
            # Cron 不支持“每 N 周”，仅建议直接使用 weekly(on=..., at=...)
            raise ValueError("@every <n>w is not supported; use weekly(on=..., at=...) instead")
        raise ValueError("Unknown unit for @every; use s/m/h/d/mo")
    # 未知宏直接返回，交由 croniter 处理（可能抛错）
    return s