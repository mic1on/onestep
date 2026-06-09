from __future__ import annotations

from onestep.resource_registry import ResourceRegistry

from . import feishu_bitable, http, memory, mysql, rabbitmq, redis, schedule, sqs, webhook


def register_builtin_resources(registry: ResourceRegistry) -> None:
    for module in (memory, schedule, webhook, http, feishu_bitable, rabbitmq, redis, sqs, mysql):
        module.register_resources(registry)
