from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from onestep.connectors.memory import MemoryQueue
from onestep.resource_registry import ResourceBuildContext, ResourceRegistry, ResourceSpecHandler

_MEMORY_FIELDS = frozenset({"type", "name", "maxsize", "batch_size", "poll_interval_s"})


def register_resources(registry: ResourceRegistry) -> None:
    registry.register_resource_type(
        ResourceSpecHandler(
            type="memory",
            allowed_fields=_MEMORY_FIELDS,
            build=_build_memory,
        )
    )


def _build_memory(ctx: ResourceBuildContext, spec: Mapping[str, Any]) -> MemoryQueue:
    return MemoryQueue(
        ctx.resource_name(spec),
        maxsize=spec.get("maxsize", 0),
        batch_size=spec.get("batch_size", 100),
        poll_interval_s=spec.get("poll_interval_s", 0.1),
    )
