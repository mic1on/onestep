from __future__ import annotations

import importlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from importlib import metadata as importlib_metadata
from typing import Any

_ENTRY_POINT_GROUP = "onestep.resources"
CATALOG_ROLES = frozenset({"connector", "source", "sink", "state_store", "cursor_store"})
CATALOG_FIELD_TYPES = frozenset(
    {"string", "string_list", "integer", "number", "boolean", "mapping", "json", "ref"}
)


@dataclass(frozen=True)
class ResourceCatalogField:
    name: str
    type: str
    required: bool = False
    default: Any = None
    secret: bool = False
    label: str | None = None
    options: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", normalize_resource_type(require_non_empty_string(self.name, field="field.name")))
        normalized_type = normalize_resource_type(require_non_empty_string(self.type, field=f"{self.name}.type"))
        if normalized_type not in CATALOG_FIELD_TYPES:
            raise ValueError(f"unsupported catalog field type {normalized_type!r} for {self.name!r}")
        object.__setattr__(self, "type", normalized_type)
        object.__setattr__(self, "required", bool(self.required))
        object.__setattr__(self, "secret", bool(self.secret))
        if self.label is not None:
            object.__setattr__(self, "label", require_non_empty_string(self.label, field=f"{self.name}.label"))
        object.__setattr__(
            self,
            "options",
            tuple(require_non_empty_string(option, field=f"{self.name}.options") for option in self.options),
        )

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "name": self.name,
            "type": self.type,
            "required": self.required,
            "secret": self.secret,
        }
        if self.default is not None:
            result["default"] = self.default
        if self.label is not None:
            result["label"] = self.label
        if self.options:
            result["options"] = list(self.options)
        return result


@dataclass(frozen=True)
class ResourceCatalogEntry:
    type: str
    roles: tuple[str, ...]
    fields: tuple[ResourceCatalogField, ...] = ()
    connector_types: tuple[str, ...] = ()
    topology_fields: tuple[str, ...] = ()
    label: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "type", normalize_resource_type(require_non_empty_string(self.type, field="type")))
        roles = tuple(normalize_resource_type(require_non_empty_string(role, field=f"{self.type}.roles")) for role in self.roles)
        if not roles:
            raise ValueError(f"catalog entry {self.type!r} must define at least one role")
        unknown_roles = sorted(role for role in roles if role not in CATALOG_ROLES)
        if unknown_roles:
            raise ValueError(f"unsupported catalog role(s) for {self.type!r}: {', '.join(unknown_roles)}")
        if len(set(roles)) != len(roles):
            raise ValueError(f"catalog entry {self.type!r} must not contain duplicate roles")
        object.__setattr__(self, "roles", roles)
        object.__setattr__(self, "fields", tuple(self.fields))
        field_names = [field.name for field in self.fields]
        if len(set(field_names)) != len(field_names):
            raise ValueError(f"catalog entry {self.type!r} must not contain duplicate fields")
        object.__setattr__(
            self,
            "connector_types",
            tuple(normalize_resource_type(require_non_empty_string(item, field=f"{self.type}.connector_types")) for item in self.connector_types),
        )
        object.__setattr__(
            self,
            "topology_fields",
            tuple(normalize_resource_type(require_non_empty_string(item, field=f"{self.type}.topology_fields")) for item in self.topology_fields),
        )
        unknown_topology_fields = sorted(set(self.topology_fields) - set(field_names))
        if unknown_topology_fields:
            raise ValueError(
                f"catalog entry {self.type!r} references unknown topology field(s): "
                + ", ".join(unknown_topology_fields)
            )
        if self.label is not None:
            object.__setattr__(self, "label", require_non_empty_string(self.label, field=f"{self.type}.label"))

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "roles": list(self.roles),
            "label": self.label or self.type,
            "connector_types": list(self.connector_types),
            "fields": [field.as_dict() for field in self.fields],
            "topology_fields": list(self.topology_fields),
        }


@dataclass(frozen=True)
class ResourceSpecHandler:
    type: str
    catalog: ResourceCatalogEntry
    allowed_fields: frozenset[str] | None
    build: Callable[["ResourceBuildContext", Mapping[str, Any]], Any]
    validate: Callable[["ResourceValidationContext", Mapping[str, Any]], None] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "type", normalize_resource_type(require_non_empty_string(self.type, field="type")))
        if not isinstance(self.catalog, ResourceCatalogEntry):
            raise TypeError("ResourceSpecHandler.catalog must be a ResourceCatalogEntry")
        if self.catalog.type != self.type:
            raise ValueError(
                f"resource handler type {self.type!r} must match catalog type {self.catalog.type!r}"
            )
        if self.allowed_fields is not None:
            object.__setattr__(self, "allowed_fields", frozenset(self.allowed_fields))


@dataclass
class ResourceRegistry:
    _handlers: dict[str, ResourceSpecHandler] = field(default_factory=dict)
    _loaded_entry_points: set[str] = field(default_factory=set)

    def register_resource_type(self, handler: ResourceSpecHandler) -> None:
        resource_type = normalize_resource_type(handler.type)
        existing = self._handlers.get(resource_type)
        if existing is not None:
            if existing == handler:
                return
            raise ValueError(f"resource type {resource_type!r} is already registered")
        self._handlers[resource_type] = handler

    def get_resource_handler(self, resource_type: str) -> ResourceSpecHandler | None:
        return self._handlers.get(normalize_resource_type(resource_type))

    def get_resource_catalog_entry(self, resource_type: str) -> ResourceCatalogEntry | None:
        handler = self.get_resource_handler(resource_type)
        return None if handler is None else handler.catalog

    def catalog_entries(self, role: str | None = None) -> tuple[ResourceCatalogEntry, ...]:
        if role is None:
            return tuple(handler.catalog for _, handler in sorted(self._handlers.items()))
        normalized_role = normalize_resource_type(role)
        if normalized_role not in CATALOG_ROLES:
            raise ValueError(f"unsupported catalog role {normalized_role!r}")
        return tuple(
            handler.catalog
            for _, handler in sorted(self._handlers.items())
            if normalized_role in handler.catalog.roles
        )

    def handlers(self) -> Mapping[str, ResourceSpecHandler]:
        return dict(self._handlers)

    def has_entry_point_loaded(self, identity: str) -> bool:
        return identity in self._loaded_entry_points

    def mark_entry_point_loaded(self, identity: str) -> None:
        self._loaded_entry_points.add(identity)


@dataclass(frozen=True)
class ResourceBuildContext:
    name: str
    type: str
    field: str
    _resolve: Callable[[str], Any]

    def resolve(self, name: str) -> Any:
        return self._resolve(name)

    def resolve_dependency(self, spec: Mapping[str, Any], key: str) -> Any:
        return self.resolve(require_string(spec, key))

    def resource_name(self, spec: Mapping[str, Any], *, fallback: str | None = None, key: str = "name") -> str:
        return resource_name(spec, fallback=self.name if fallback is None else fallback, key=key)

    def require_string(self, mapping: Mapping[str, Any], key: str) -> str:
        return require_string(mapping, key)

    def string_value(self, value: Any, *, field: str) -> str:
        return string_value(value, field=field)

    def string_list(self, value: Any, *, field: str) -> list[str]:
        return string_list(value, field=field)

    def require_mapping(self, mapping: Mapping[str, Any], key: str) -> dict[str, Any]:
        return require_mapping(mapping, key)

    def optional_mapping(self, value: Any, *, field: str) -> dict[str, Any] | None:
        return optional_mapping(value, field=field)

    def mapping_value(self, value: Any, *, field: str) -> dict[str, Any]:
        return mapping_value(value, field=field)

    def optional_ref(self, value: Any, *, field: str) -> Any:
        return optional_ref(value, field=field)

    def is_cursor_store(self, value: Any) -> bool:
        return is_cursor_store(value)

    def is_state_store(self, value: Any) -> bool:
        return is_state_store(value)


@dataclass(frozen=True)
class ResourceValidationContext:
    name: str
    type: str
    field: str

    def require_string(self, mapping: Mapping[str, Any], key: str) -> str:
        return require_string(mapping, key)

    def string_value(self, value: Any, *, field: str) -> str:
        return string_value(value, field=field)

    def require_non_empty_string_list(self, mapping: Mapping[str, Any], key: str, *, field: str) -> list[str]:
        return require_non_empty_string_list(mapping, key, field=field)

    def validate_positive_number(self, value: Any, *, field: str) -> None:
        validate_positive_number(value, field=field)

    def validate_non_negative_number(self, value: Any, *, field: str) -> None:
        validate_non_negative_number(value, field=field)

    def validate_positive_integer(self, value: Any, *, field: str) -> None:
        validate_positive_integer(value, field=field)

    def validate_unknown_fields(self, mapping: Mapping[str, Any], allowed: frozenset[str], *, field: str) -> None:
        validate_unknown_fields(mapping, allowed, field=field)


_DEFAULT_REGISTRY = ResourceRegistry()


def default_resource_registry() -> ResourceRegistry:
    return _DEFAULT_REGISTRY


def register_resource_type(handler: ResourceSpecHandler) -> None:
    default_resource_registry().register_resource_type(handler)


def get_resource_handler(resource_type: str) -> ResourceSpecHandler | None:
    return default_resource_registry().get_resource_handler(resource_type)


def get_resource_catalog_entry(resource_type: str) -> ResourceCatalogEntry | None:
    return default_resource_registry().get_resource_catalog_entry(resource_type)


def load_resource_plugins(registry: ResourceRegistry | None = None) -> None:
    target = registry or default_resource_registry()
    for entry_point in _resource_entry_points():
        identity = _entry_point_identity(entry_point)
        if target.has_entry_point_loaded(identity):
            continue
        try:
            register = entry_point.load()
            if not callable(register):
                raise TypeError(f"entry point {entry_point.name!r} did not resolve to a callable")
            register(target)
        except Exception as exc:
            raise RuntimeError(f"failed to load onestep resource plugin {entry_point.name!r}") from exc
        target.mark_entry_point_loaded(identity)


def _resource_entry_points() -> Sequence[Any]:
    entry_points = importlib_metadata.entry_points()
    if hasattr(entry_points, "select"):
        return tuple(entry_points.select(group=_ENTRY_POINT_GROUP))
    return tuple(entry_points.get(_ENTRY_POINT_GROUP, ()))


def _entry_point_identity(entry_point: Any) -> str:
    group = getattr(entry_point, "group", _ENTRY_POINT_GROUP)
    name = getattr(entry_point, "name", "")
    value = getattr(entry_point, "value", "")
    return f"{group}:{name}:{value}"


def normalize_resource_type(value: str) -> str:
    return value.strip().lower().replace("-", "_").replace(".", "_")


def load_ref(ref: str) -> Any:
    module_name, separator, attr_path = ref.partition(":")
    if not separator or not module_name or not attr_path:
        raise ValueError(f"invalid ref {ref!r}; expected 'package.module:attr'")
    module = importlib.import_module(module_name)
    value: Any = module
    for part in attr_path.split("."):
        value = getattr(value, part)
    return value


def resource_name(spec: Mapping[str, Any], *, fallback: str, key: str = "name") -> str:
    value = spec.get(key)
    if value is None:
        return fallback
    return string_value(value, field=key)


def require_non_empty_string(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"'{field}' must be a non-empty string")
    return value


def require_string(mapping: Mapping[str, Any], key: str) -> str:
    return require_non_empty_string(mapping.get(key), field=key)


def optional_string(mapping: Mapping[str, Any], key: str) -> str | None:
    value = mapping.get(key)
    if value is None:
        return None
    return string_value(value, field=key)


def string_value(value: Any, *, field: str) -> str:
    return require_non_empty_string(value, field=field)


def string_list(value: Any, *, field: str) -> list[str]:
    if value is None:
        raise ValueError(f"'{field}' must be provided")
    if isinstance(value, str):
        return [string_value(value, field=field)]
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise TypeError(f"'{field}' must be a string or list of strings")
    return [string_value(item, field=field) for item in value]


def require_non_empty_string_list(mapping: Mapping[str, Any], key: str, *, field: str) -> list[str]:
    value = mapping.get(key)
    if value is None:
        raise ValueError(f"'{field}' must be a non-empty list of strings")
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise TypeError(f"'{field}' must be a non-empty list of strings")
    items = [string_value(item, field=f"{field}[{index}]").strip() for index, item in enumerate(value)]
    if not items:
        raise ValueError(f"'{field}' must be a non-empty list of strings")
    if len(set(items)) != len(items):
        raise ValueError(f"'{field}' must not contain duplicate field names")
    return items


def require_mapping(mapping: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = mapping.get(key)
    if not isinstance(value, Mapping):
        raise TypeError(f"'{key}' must be a mapping")
    return dict(value)


def optional_mapping(value: Any, *, field: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise TypeError(f"'{field}' must be a mapping")
    return dict(value)


def mapping_value(value: Any, *, field: str) -> dict[str, Any]:
    return optional_mapping(value, field=field) or {}


def optional_ref(value: Any, *, field: str) -> Any:
    if value is None:
        return None
    if callable(value):
        return value
    if isinstance(value, str):
        return load_ref(value)
    raise TypeError(f"'{field}' must be a callable or ref string")


def is_cursor_store(value: Any) -> bool:
    return hasattr(value, "load") and hasattr(value, "save")


def is_state_store(value: Any) -> bool:
    return is_cursor_store(value) and hasattr(value, "delete")


def validate_unknown_fields(mapping: Mapping[str, Any], allowed: frozenset[str], *, field: str) -> None:
    unexpected = sorted(str(key) for key in mapping.keys() if key not in allowed)
    if unexpected:
        raise ValueError(f"unsupported fields for {field}: {', '.join(unexpected)}")


def validate_positive_number(value: Any, *, field: str) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"'{field}' must be a number")
    if value <= 0:
        raise ValueError(f"'{field}' must be > 0")


def validate_non_negative_number(value: Any, *, field: str) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"'{field}' must be a number")
    if value < 0:
        raise ValueError(f"'{field}' must be >= 0")


def validate_positive_integer(value: Any, *, field: str) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"'{field}' must be an integer")
    if value < 1:
        raise ValueError(f"'{field}' must be >= 1")
