"""JSON Schema for the ``onestep/v1alpha1`` YAML app format.

The schema is *derived*, never hand-written: task/emit/hook shapes come from the
same ``_STRICT_*`` field sets that strict validation enforces, and every
resource type comes from the live resource catalog. That means a connector that
registers a new resource type (or adds a catalog field) appears in the schema
without anyone editing a second file.

``docs/public/schema/v1alpha1.json`` is the published copy; a contract test
asserts it matches this generator so the URL in a user's editor cannot drift
from the installed runtime.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .config import (
    _STRICT_API_VERSION,
    _STRICT_APP_FIELDS,
    _STRICT_APP_HOOK_FIELDS,
    _STRICT_EMIT_BINDING_FIELDS,
    _STRICT_EMIT_ROUTE_FIELDS,
    _STRICT_HANDLER_FIELDS,
    _STRICT_KIND,
    _STRICT_TASK_FIELDS,
    _STRICT_TASK_HOOK_FIELDS,
    _STRICT_TOP_LEVEL_FIELDS,
)
from .resource_registry import CATALOG_FIELD_TYPES

SCHEMA_ID = "https://onestep.code05.com/schema/v1alpha1.json"
SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"

# Catalog field type -> JSON Schema fragment. ``json`` accepts anything, and a
# ``ref`` is a name pointing at another resource in the same document.
_FIELD_TYPE_SCHEMAS: dict[str, dict[str, Any]] = {
    "string": {"type": "string"},
    "string_list": {"type": "array", "items": {"type": "string"}},
    "integer": {"type": "integer"},
    "number": {"type": "number"},
    "boolean": {"type": "boolean"},
    "mapping": {"type": "object"},
    "json": {},
    "ref": {"type": "string"},
}


def _resource_name_schema() -> dict[str, Any]:
    return {"type": "string", "description": "Name of a resource defined in this document."}


def _handler_schema() -> dict[str, Any]:
    return {
        "oneOf": [
            {"type": "string", "description": "Importable reference, e.g. package.module:function."},
            {
                "type": "object",
                "properties": {
                    "ref": {"type": "string"},
                    "params": {"type": "object"},
                },
                "required": ["ref"],
                "additionalProperties": False,
            },
        ],
        "description": "Python callable executed for each delivery.",
    }


def _emit_binding_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "sink": _resource_name_schema(),
            "transform": _handler_schema(),
        },
        "required": ["sink"],
        "additionalProperties": False,
    }


def _emit_branch_schema() -> dict[str, Any]:
    binding = {"$ref": "#/$defs/emitBinding"}
    return {
        "oneOf": [
            {"type": "string"},
            {"type": "array", "items": {"oneOf": [{"type": "string"}, binding]}},
            binding,
        ]
    }


def _emit_schema() -> dict[str, Any]:
    return {
        "oneOf": [
            {"type": "string"},
            {"type": "array", "items": {"oneOf": [{"type": "string"}, {"$ref": "#/$defs/emitBinding"}]}},
            {"$ref": "#/$defs/emitBinding"},
            {
                "type": "object",
                "properties": {
                    "when": _handler_schema(),
                    "then": _emit_branch_schema(),
                    "otherwise": _emit_branch_schema(),
                },
                "required": ["when", "then"],
                "additionalProperties": False,
            },
        ],
        "description": "Sink, emit binding, or conditional route.",
    }


def _ref_collection_schema() -> dict[str, Any]:
    entry = _handler_schema()
    return {"oneOf": [entry, {"type": "array", "items": entry}]}


def _hooks_schema(fields: frozenset[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {name: _ref_collection_schema() for name in sorted(fields)},
        "additionalProperties": False,
    }


def _retry_schema() -> dict[str, Any]:
    return {
        "oneOf": [
            {"type": "string"},
            {
                "type": "object",
                "properties": {"type": {"type": "string"}},
                "required": ["type"],
                "additionalProperties": True,
            },
        ],
        "description": "Retry policy name or mapping; strict validation checks per-type fields.",
    }


def _task_schema() -> dict[str, Any]:
    properties: dict[str, Any] = {
        "name": {"type": "string"},
        "description": {"type": "string"},
        "source": _resource_name_schema(),
        "emit": _emit_schema(),
        "dead_letter": {
            "oneOf": [_resource_name_schema(), {"type": "array", "items": _resource_name_schema()}]
        },
        "config": {"type": "object"},
        "metadata": {"type": "object"},
        "handler": _handler_schema(),
        "hooks": _hooks_schema(_STRICT_TASK_HOOK_FIELDS),
        "concurrency": {"type": "integer", "minimum": 1},
        "timeout_s": {"type": "number", "exclusiveMinimum": 0},
        "retry": _retry_schema(),
    }
    assert set(properties) == set(_STRICT_TASK_FIELDS), "task schema drifted from strict fields"
    return {
        "type": "object",
        "properties": properties,
        "required": ["name"],
        "additionalProperties": False,
        "anyOf": [{"required": ["handler"]}, {"required": ["emit"]}],
    }


def _jsonable(value: Any) -> Any:
    """Convert a catalog default into JSON-native data.

    Catalog defaults may be tuples (``ResourceCatalogField("methods", ..., default=("POST",))``).
    ``json.dumps`` would serialize those as arrays, so a schema parsed back from
    the published file would not compare equal to the in-memory document. That
    made the drift guard environment-dependent; normalizing here keeps the
    generated document identical to its serialized form.
    """
    if isinstance(value, Mapping):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_jsonable(item) for item in value)
    return value


def _resource_field_schema(field: Any) -> dict[str, Any]:
    schema = dict(_FIELD_TYPE_SCHEMAS[field.type])
    if field.default is not None:
        schema["default"] = _jsonable(field.default)
    if field.label:
        schema["title"] = field.label
    if field.options:
        schema["enum"] = list(field.options)
    if field.type == "ref":
        schema["description"] = "Name of another resource in this document."
    if field.secret:
        schema["description"] = "Secret value; prefer an environment variable reference."
    return schema


def _resource_definitions(registry: Any) -> dict[str, Any]:
    """Build one ``$defs`` entry per installed resource type.

    Two catalog properties are deliberately NOT used here:

    * ``required`` — the catalog over-claims it. It lists ``dsn`` as required for
      ``mysql`` and ``path`` for ``webhook``, yet ``onestep check --strict``
      accepts those documents (the handler defers the check to build time). A
      schema that rejected them would refuse valid configs, so required-ness is
      left to ``onestep check --strict``, which is authoritative.
    * ``fields`` alone — for ``mysql``/``postgres``/``rabbitmq``/``redis`` the
      catalog also advertises ``host``/``username``/``password``, which strict
      mode *rejects* (``unsupported fields for resources.db: ...``). Using them
      would make the schema accept documents the runtime refuses.

    ``handler.allowed_fields`` is the set strict validation actually enforces, so
    it defines the properties and ``additionalProperties: false`` — which is what
    catches a typo like ``concurrencyy``.
    """
    defs: dict[str, Any] = {}
    references: list[dict[str, Any]] = []
    for entry in registry.catalog_entries(None):
        handler = registry.get_resource_handler(entry.type)
        if handler is None or handler.allowed_fields is None:
            # No strict field contract: accept any mapping rather than guess.
            defs[f"resource_{entry.type}"] = {
                "type": "object",
                "title": entry.label or entry.type,
                "properties": {"type": {"const": entry.type}},
            }
            references.append({"$ref": f"#/$defs/resource_{entry.type}"})
            continue

        by_name = {field.name: field for field in entry.fields}
        properties: dict[str, Any] = {
            "type": {"const": entry.type, "description": entry.label or entry.type}
        }
        for name in sorted(set(handler.allowed_fields) - {"type"}):
            field = by_name.get(name)
            if field is None or field.type not in CATALOG_FIELD_TYPES:
                properties[name] = {}
                continue
            properties[name] = _resource_field_schema(field)
        defs[f"resource_{entry.type}"] = {
            "type": "object",
            "title": entry.label or entry.type,
            "properties": properties,
            "additionalProperties": False,
        }
        references.append({"$ref": f"#/$defs/resource_{entry.type}"})
    defs["resource"] = {"oneOf": references} if references else {"type": "object"}
    return defs


def _official_registry() -> Any:
    """Build a fresh registry containing only built-ins plus installed plugins.

    The global default registry is process-wide and mutable: any caller (or
    test) that calls ``register_resource_type`` adds a type that would then
    appear in the generated schema. Building a private registry keeps the
    published schema a function of the installed plugins alone, so it does not
    depend on what else ran earlier in the same process — which matters because
    CI runs the whole suite in one process.
    """
    from .resource_registry import ResourceRegistry, load_resource_plugins
    from .resources import register_builtin_resources

    registry = ResourceRegistry()
    register_builtin_resources(registry)
    load_resource_plugins(registry)
    return registry


def build_app_schema(*, registry: Any | None = None) -> dict[str, Any]:
    """Return the JSON Schema document for ``onestep/v1alpha1`` YAML apps.

    ``registry`` defaults to the shipped resource set; pass one explicitly to
    generate a schema for a custom connector set.
    """
    registry = registry if registry is not None else _official_registry()
    properties: dict[str, Any] = {
        "$schema": {
            "type": "string",
            "description": "JSON Schema self-reference. Documentation only; never affects runtime.",
        },
        "apiVersion": {"const": _STRICT_API_VERSION},
        "kind": {"const": _STRICT_KIND},
        "app": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "shutdown_timeout_s": {"type": "number", "exclusiveMinimum": 0},
                "config": {"type": "object"},
                "state": _resource_name_schema(),
                "logging": {
                    "type": "object",
                    "properties": {
                        "level": {"type": "string"},
                        "format": {"enum": ["text", "json"]},
                    },
                    "additionalProperties": False,
                },
                "env_file": {"type": "string"},
                "strict_env": {"type": "boolean"},
                "failure_capture": {
                    "type": "object",
                    "properties": {
                        "directory": {"type": "string"},
                        "mode": {"enum": ["terminal", "all"]},
                        "max_bytes": {"type": "integer", "minimum": 1},
                        "redact_paths": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["directory"],
                    "additionalProperties": False,
                },
            },
            "additionalProperties": False,
        },
        "reporter": {
            "oneOf": [{"type": "boolean"}, {"type": "object"}],
            "description": "Control-plane reporter; disabled by default.",
        },
        "resources": {"type": "object", "additionalProperties": {"$ref": "#/$defs/resource"}},
        "connectors": {"type": "object", "additionalProperties": {"$ref": "#/$defs/resource"}},
        "sources": {"type": "object", "additionalProperties": {"$ref": "#/$defs/resource"}},
        "sinks": {"type": "object", "additionalProperties": {"$ref": "#/$defs/resource"}},
        "hooks": _hooks_schema(_STRICT_APP_HOOK_FIELDS),
        "tasks": {"type": "array", "items": {"$ref": "#/$defs/task"}},
    }
    for legacy in ("name", "shutdown_timeout_s", "config", "state"):
        properties[legacy] = {"description": "Legacy top-level field; prefer 'app'."}
    assert set(properties) == set(_STRICT_TOP_LEVEL_FIELDS), "top-level schema drifted from strict fields"
    assert set(properties["app"]["properties"]) == set(_STRICT_APP_FIELDS), (
        "app schema drifted from strict fields"
    )

    defs = _resource_definitions(registry)
    defs["task"] = _task_schema()
    defs["emitBinding"] = _emit_binding_schema()
    defs["handler"] = _handler_schema()
    assert set(defs["emitBinding"]["properties"]) == set(_STRICT_EMIT_BINDING_FIELDS)
    assert set(defs["handler"]["oneOf"][1]["properties"]) == set(_STRICT_HANDLER_FIELDS)
    assert set(defs["task"]["properties"]["emit"]["oneOf"][3]["properties"]) == set(
        _STRICT_EMIT_ROUTE_FIELDS
    )

    return {
        "$schema": SCHEMA_DIALECT,
        "$id": SCHEMA_ID,
        "title": "onestep/v1alpha1 App",
        "description": (
            "YAML task definition for the onestep async task runtime. "
            "Generated from the installed resource catalog and strict field contracts."
        ),
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
        "$defs": defs,
    }


def schema_json(*, indent: int = 2) -> str:
    """Serialize the schema with stable ordering for byte-comparable output."""
    import json

    return json.dumps(build_app_schema(), indent=indent, sort_keys=False) + "\n"


__all__ = ["SCHEMA_DIALECT", "SCHEMA_ID", "build_app_schema", "schema_json"]
