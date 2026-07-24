from __future__ import annotations

import copy
from functools import lru_cache
from typing import Any

from onestep import ResourceCatalogEntry, get_resource_handler, load_resource_catalog


@lru_cache(maxsize=1)
def catalog_entries() -> tuple[ResourceCatalogEntry, ...]:
    return load_resource_catalog()


@lru_cache(maxsize=1)
def catalog_by_type() -> dict[str, ResourceCatalogEntry]:
    return {entry.type: entry for entry in catalog_entries()}


def catalog_response() -> dict[str, Any]:
    return {"resources": [entry.as_dict() for entry in catalog_entries()]}


def require_catalog_entry(resource_type: str) -> ResourceCatalogEntry:
    entry = catalog_by_type().get(resource_type)
    if entry is None:
        raise ValueError(f"unsupported resource type: {resource_type}")
    return entry


def connector_types() -> frozenset[str]:
    return frozenset(entry.type for entry in catalog_entries() if "connector" in entry.roles)


def is_connector_type(resource_type: str) -> bool:
    return resource_type in connector_types()


def secret_fields(resource_type: str) -> frozenset[str]:
    entry = require_catalog_entry(resource_type)
    return frozenset(field.name for field in entry.fields if field.secret)


def runtime_fields(resource_type: str) -> frozenset[str]:
    require_catalog_entry(resource_type)
    handler = get_resource_handler(resource_type)
    if handler is None:
        raise ValueError(f"unsupported resource type: {resource_type}")
    if handler.allowed_fields is None:
        return frozenset(field.name for field in handler.catalog.fields)
    return frozenset(field for field in handler.allowed_fields if field != "type")


def runtime_config_fields(resource_type: str) -> frozenset[str]:
    return runtime_fields(resource_type) - secret_fields(resource_type)


def runtime_secret_fields(resource_type: str) -> frozenset[str]:
    return runtime_fields(resource_type) & secret_fields(resource_type)


def resource_needs_connector(resource_type: str) -> bool:
    return bool(require_catalog_entry(resource_type).connector_types)


def catalog_field_default(resource_type: str, field_name: str) -> Any:
    entry = require_catalog_entry(resource_type)
    for field in entry.fields:
        if field.name == field_name:
            return copy.deepcopy(field.default)
    return None
