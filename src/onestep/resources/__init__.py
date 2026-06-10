from __future__ import annotations

from onestep.resource_registry import ResourceRegistry

from . import http, memory, rabbitmq, redis, schedule, webhook


def register_builtin_resources(registry: ResourceRegistry) -> None:
    for module in (memory, schedule, webhook, http, rabbitmq, redis):
        module.register_resources(registry)
