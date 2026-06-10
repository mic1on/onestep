from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from onestep.connectors.schedule import DEFAULT_MAX_QUEUED_RUNS, CronSource, IntervalSource
from onestep.resource_registry import (
    ResourceBuildContext,
    ResourceRegistry,
    ResourceSpecHandler,
    ResourceValidationContext,
)

_INTERVAL_FIELDS = frozenset(
    {
        "type",
        "name",
        "days",
        "hours",
        "minutes",
        "seconds",
        "payload",
        "immediate",
        "overlap",
        "timezone",
        "timezone_name",
        "poll_interval_s",
        "max_queued_runs",
    }
)
_CRON_FIELDS = frozenset(
    {
        "type",
        "name",
        "expression",
        "payload",
        "immediate",
        "overlap",
        "timezone",
        "timezone_name",
        "poll_interval_s",
        "max_queued_runs",
    }
)


def register_resources(registry: ResourceRegistry) -> None:
    registry.register_resource_type(
        ResourceSpecHandler(
            type="interval",
            allowed_fields=_INTERVAL_FIELDS,
            build=_build_interval,
            validate=_validate_schedule,
        )
    )
    registry.register_resource_type(
        ResourceSpecHandler(
            type="cron",
            allowed_fields=_CRON_FIELDS,
            build=_build_cron,
            validate=_validate_schedule,
        )
    )


def _build_interval(ctx: ResourceBuildContext, spec: Mapping[str, Any]) -> IntervalSource:
    return IntervalSource.every(
        days=spec.get("days", 0),
        hours=spec.get("hours", 0),
        minutes=spec.get("minutes", 0),
        seconds=spec.get("seconds", 0),
        payload=spec.get("payload"),
        immediate=spec.get("immediate", False),
        overlap=spec.get("overlap", "allow"),
        timezone=spec.get("timezone"),
        timezone_name=spec.get("timezone_name"),
        poll_interval_s=spec.get("poll_interval_s"),
        max_queued_runs=spec.get("max_queued_runs", DEFAULT_MAX_QUEUED_RUNS),
        name=ctx.resource_name(spec),
    )


def _build_cron(ctx: ResourceBuildContext, spec: Mapping[str, Any]) -> CronSource:
    return CronSource(
        ctx.require_string(spec, "expression"),
        payload=spec.get("payload"),
        immediate=spec.get("immediate", False),
        overlap=spec.get("overlap", "allow"),
        timezone=spec.get("timezone"),
        timezone_name=spec.get("timezone_name"),
        poll_interval_s=spec.get("poll_interval_s", 1.0),
        max_queued_runs=spec.get("max_queued_runs", DEFAULT_MAX_QUEUED_RUNS),
        name=ctx.resource_name(spec),
    )


def _validate_schedule(ctx: ResourceValidationContext, spec: Mapping[str, Any]) -> None:
    ctx.validate_non_negative_number(spec.get("poll_interval_s"), field=f"{ctx.field}.poll_interval_s")
    if "max_queued_runs" in spec:
        if spec.get("max_queued_runs") is None:
            raise ValueError(f"'{ctx.field}.max_queued_runs' must be >= 1")
        ctx.validate_positive_integer(spec.get("max_queued_runs"), field=f"{ctx.field}.max_queued_runs")
