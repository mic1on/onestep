from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from importlib import metadata as importlib_metadata
from typing import TYPE_CHECKING, Any

from .resource_registry import normalize_resource_type, optional_string, validate_unknown_fields

if TYPE_CHECKING:
    from .app import OneStepApp

_ENTRY_POINT_GROUP = "onestep.reporters"


@dataclass(frozen=True)
class ReporterSpecHandler:
    type: str
    allowed_fields: frozenset[str] | None
    build: Callable[["ReporterBuildContext", Mapping[str, Any]], Any]
    validate: Callable[["ReporterValidationContext", Mapping[str, Any]], None] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "type", normalize_reporter_type(self.type))
        if self.allowed_fields is not None:
            object.__setattr__(self, "allowed_fields", frozenset(self.allowed_fields))


@dataclass(frozen=True)
class ReporterBuildContext:
    app: "OneStepApp"
    field: str = "reporter"

    def optional_string(self, mapping: Mapping[str, Any], key: str) -> str | None:
        return optional_string(mapping, key)


@dataclass(frozen=True)
class ReporterValidationContext:
    field: str = "reporter"


@dataclass
class ReporterRegistry:
    _handlers: dict[str, ReporterSpecHandler] = field(default_factory=dict)
    _loaded_entry_points: set[str] = field(default_factory=set)

    def register_reporter_type(self, handler: ReporterSpecHandler) -> None:
        reporter_type = normalize_reporter_type(handler.type)
        existing = self._handlers.get(reporter_type)
        if existing is not None:
            if existing == handler:
                return
            raise ValueError(f"reporter type {reporter_type!r} is already registered")
        self._handlers[reporter_type] = handler

    def get_reporter_handler(self, reporter_type: str) -> ReporterSpecHandler | None:
        return self._handlers.get(normalize_reporter_type(reporter_type))

    def handlers(self) -> Mapping[str, ReporterSpecHandler]:
        return dict(self._handlers)

    def has_entry_point_loaded(self, identity: str) -> bool:
        return identity in self._loaded_entry_points

    def mark_entry_point_loaded(self, identity: str) -> None:
        self._loaded_entry_points.add(identity)


_DEFAULT_REGISTRY = ReporterRegistry()


def default_reporter_registry() -> ReporterRegistry:
    return _DEFAULT_REGISTRY


def register_reporter_type(handler: ReporterSpecHandler) -> None:
    default_reporter_registry().register_reporter_type(handler)


def get_reporter_handler(reporter_type: str) -> ReporterSpecHandler | None:
    return default_reporter_registry().get_reporter_handler(reporter_type)


def load_reporter_plugins(registry: ReporterRegistry | None = None) -> None:
    target = registry or default_reporter_registry()
    for entry_point in _reporter_entry_points():
        identity = _entry_point_identity(entry_point)
        if target.has_entry_point_loaded(identity):
            continue
        try:
            register = entry_point.load()
            if not callable(register):
                raise TypeError(f"entry point {entry_point.name!r} did not resolve to a callable")
            register(target)
        except Exception as exc:
            raise RuntimeError(f"failed to load onestep reporter plugin {entry_point.name!r}") from exc
        target.mark_entry_point_loaded(identity)


def normalize_reporter_type(value: str) -> str:
    return normalize_resource_type(value)


def missing_control_plane_plugin_error() -> RuntimeError:
    return RuntimeError(
        "control plane reporter support is provided by the onestep-control-plane plugin. "
        "Install it with `pip install 'onestep[control-plane]'`."
    )


def validate_reporter_spec(spec: Mapping[str, Any], *, registry: ReporterRegistry) -> None:
    reporter_type = str(spec.get("type") or "control_plane")
    handler = registry.get_reporter_handler(reporter_type)
    if handler is None:
        if normalize_reporter_type(reporter_type) == "control_plane":
            raise missing_control_plane_plugin_error()
        raise ValueError(f"unsupported reporter type {reporter_type!r}")
    if handler.allowed_fields is not None:
        validate_unknown_fields(spec, handler.allowed_fields, field="reporter")
    if handler.validate is not None:
        handler.validate(ReporterValidationContext(), spec)


def build_reporter(app: "OneStepApp", spec: Mapping[str, Any], *, registry: ReporterRegistry) -> Any:
    reporter_type = str(spec.get("type") or "control_plane")
    handler = registry.get_reporter_handler(reporter_type)
    if handler is None:
        if normalize_reporter_type(reporter_type) == "control_plane":
            raise missing_control_plane_plugin_error()
        raise ValueError(f"unsupported reporter type {reporter_type!r}")
    return handler.build(ReporterBuildContext(app=app), spec)


def _reporter_entry_points() -> Sequence[Any]:
    entry_points = importlib_metadata.entry_points()
    if hasattr(entry_points, "select"):
        return tuple(entry_points.select(group=_ENTRY_POINT_GROUP))
    return tuple(entry_points.get(_ENTRY_POINT_GROUP, ()))


def _entry_point_identity(entry_point: Any) -> str:
    group = getattr(entry_point, "group", _ENTRY_POINT_GROUP)
    name = getattr(entry_point, "name", "")
    value = getattr(entry_point, "value", "")
    return f"{group}:{name}:{value}"


__all__ = [
    "ReporterBuildContext",
    "ReporterRegistry",
    "ReporterSpecHandler",
    "ReporterValidationContext",
    "build_reporter",
    "default_reporter_registry",
    "get_reporter_handler",
    "load_reporter_plugins",
    "missing_control_plane_plugin_error",
    "normalize_reporter_type",
    "register_reporter_type",
    "validate_reporter_spec",
]
