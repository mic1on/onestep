from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from onestep.connectors.memory import MemoryQueue
from onestep.resource_registry import (
    ResourceCatalogEntry,
    ResourceCatalogField,
    ResourceBuildContext,
    ResourceRegistry,
    ResourceSpecHandler,
    ResourceValidationContext,
)

_MEMORY_FIELDS = frozenset({"type", "name", "maxsize", "batch_size", "poll_interval_s"})
_MEMORY_CATALOG = ResourceCatalogEntry(
    type="memory",
    roles=("source", "sink"),
    label="Memory Queue",
    fields=(
        ResourceCatalogField("name", "string"),
        ResourceCatalogField("maxsize", "integer", required=True, default=0),
        ResourceCatalogField("batch_size", "integer", default=100),
        ResourceCatalogField("poll_interval_s", "number", default=0.1),
    ),
    topology_fields=("maxsize", "batch_size", "poll_interval_s"),
)


def register_resources(registry: ResourceRegistry) -> None:
    registry.register_resource_type(
        ResourceSpecHandler(
            type="memory",
            catalog=_MEMORY_CATALOG,
            allowed_fields=_MEMORY_FIELDS,
            build=_build_memory,
            validate=_validate_memory,
        )
    )


def _build_memory(ctx: ResourceBuildContext, spec: Mapping[str, Any]) -> MemoryQueue:
    return MemoryQueue(
        ctx.resource_name(spec),
        maxsize=spec.get("maxsize", 0),
        batch_size=spec.get("batch_size", 100),
        poll_interval_s=spec.get("poll_interval_s", 0.1),
    )


def _validate_memory(ctx: ResourceValidationContext, spec: Mapping[str, Any]) -> None:
    if "maxsize" not in spec or spec.get("maxsize") is None:
        raise ValueError(f"{ctx.field}.maxsize is required in strict mode")
    ctx.validate_positive_integer(spec.get("maxsize"), field=f"{ctx.field}.maxsize")
    ctx.validate_positive_integer(spec.get("batch_size"), field=f"{ctx.field}.batch_size")
    ctx.validate_non_negative_number(spec.get("poll_interval_s"), field=f"{ctx.field}.poll_interval_s")
