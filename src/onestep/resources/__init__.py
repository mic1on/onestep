from __future__ import annotations

from onestep.resource_registry import ResourceRegistry

from . import http, memory, schedule, webhook


def register_builtin_resources(registry: ResourceRegistry) -> None:
    for module in (memory, schedule, webhook, http):
        module.register_resources(registry)
