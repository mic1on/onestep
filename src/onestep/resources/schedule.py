from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from onestep.connectors.schedule import CronSource, IntervalSource
from onestep.resource_registry import ResourceBuildContext, ResourceRegistry, ResourceSpecHandler

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
    }
)


def register_resources(registry: ResourceRegistry) -> None:
    registry.register_resource_type(
        ResourceSpecHandler(
            type="interval",
            allowed_fields=_INTERVAL_FIELDS,
            build=_build_interval,
        )
    )
    registry.register_resource_type(
        ResourceSpecHandler(
            type="cron",
            allowed_fields=_CRON_FIELDS,
            build=_build_cron,
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
        name=ctx.resource_name(spec),
    )
