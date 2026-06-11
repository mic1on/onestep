from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

import onestep.resource_registry as registry_module
from onestep.config import load_app_config, validate_app_config
from onestep.resource_registry import (
    ResourceBuildContext,
    ResourceRegistry,
    ResourceSpecHandler,
    ResourceValidationContext,
    load_resource_plugins,
    register_resource_type,
)


class FakeEntryPoint:
    group = "onestep.resources"

    def __init__(self, name: str, value: str, register):
        self.name = name
        self.value = value
        self._register = register

    def load(self):
        return self._register


class FakeEntryPoints(tuple):
    def select(self, *, group: str):
        return tuple(entry_point for entry_point in self if entry_point.group == group)


def _build_box(ctx: ResourceBuildContext, spec: Mapping[str, Any]) -> dict[str, Any]:
    return {"name": ctx.name, "value": spec.get("value")}


def _validate_box(ctx: ResourceValidationContext, spec: Mapping[str, Any]) -> None:
    ctx.require_string(spec, "value")


def _build_dependency(ctx: ResourceBuildContext, spec: Mapping[str, Any]) -> dict[str, Any]:
    return {"name": ctx.name, "value": ctx.require_string(spec, "value")}


def _build_dependent(ctx: ResourceBuildContext, spec: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "name": ctx.name,
        "dependency": ctx.resolve_dependency(spec, "connector"),
    }


_BOX_HANDLER = ResourceSpecHandler(
    type="test_registry_box",
    allowed_fields=frozenset({"type", "value"}),
    build=_build_box,
    validate=_validate_box,
)
_DEPENDENCY_HANDLER = ResourceSpecHandler(
    type="test_registry_dependency",
    allowed_fields=frozenset({"type", "value"}),
    build=_build_dependency,
)
_DEPENDENT_HANDLER = ResourceSpecHandler(
    type="test_registry_dependent",
    allowed_fields=frozenset({"type", "connector"}),
    build=_build_dependent,
)


def test_resource_registry_normalizes_type_names() -> None:
    registry = ResourceRegistry()
    handler = ResourceSpecHandler(
        type="Example-Resource.Type",
        allowed_fields=frozenset({"type"}),
        build=_build_box,
    )

    registry.register_resource_type(handler)

    assert registry.get_resource_handler("example_resource_type") == handler
    assert registry.get_resource_handler("EXAMPLE-RESOURCE.TYPE") == handler


def test_resource_registry_rejects_conflicting_duplicate_type() -> None:
    registry = ResourceRegistry()
    registry.register_resource_type(_BOX_HANDLER)
    registry.register_resource_type(_BOX_HANDLER)

    with pytest.raises(ValueError, match="resource type 'test_registry_box' is already registered"):
        registry.register_resource_type(
            ResourceSpecHandler(
                type="test_registry_box",
                allowed_fields=frozenset({"type"}),
                build=_build_dependency,
            )
        )


def test_load_resource_plugins_loads_entry_point_once(monkeypatch) -> None:
    calls: list[ResourceRegistry] = []

    def register(registry: ResourceRegistry) -> None:
        calls.append(registry)
        registry.register_resource_type(_BOX_HANDLER)

    entry_points = FakeEntryPoints((FakeEntryPoint("example", "example:register", register),))
    monkeypatch.setattr(registry_module.importlib_metadata, "entry_points", lambda: entry_points)
    registry = ResourceRegistry()

    load_resource_plugins(registry)
    load_resource_plugins(registry)

    assert calls == [registry]
    assert registry.get_resource_handler("test_registry_box") == _BOX_HANDLER


def test_load_resource_plugins_supports_legacy_entry_points_mapping(monkeypatch) -> None:
    calls: list[ResourceRegistry] = []

    def register(registry: ResourceRegistry) -> None:
        calls.append(registry)

    entry_point = FakeEntryPoint("legacy", "legacy:register", register)
    monkeypatch.setattr(
        registry_module.importlib_metadata,
        "entry_points",
        lambda: {"onestep.resources": (entry_point,)},
    )
    registry = ResourceRegistry()

    load_resource_plugins(registry)

    assert calls == [registry]


def test_load_resource_plugins_wraps_failures_and_retries(monkeypatch) -> None:
    calls = 0

    def register(_registry: ResourceRegistry) -> None:
        nonlocal calls
        calls += 1
        raise ValueError("boom")

    entry_points = FakeEntryPoints((FakeEntryPoint("broken", "broken:register", register),))
    monkeypatch.setattr(registry_module.importlib_metadata, "entry_points", lambda: entry_points)
    registry = ResourceRegistry()

    with pytest.raises(RuntimeError, match="failed to load onestep resource plugin 'broken'"):
        load_resource_plugins(registry)
    with pytest.raises(RuntimeError, match="failed to load onestep resource plugin 'broken'"):
        load_resource_plugins(registry)

    assert calls == 2


def test_load_resource_plugins_rejects_non_callable_entry_point(monkeypatch) -> None:
    entry_points = FakeEntryPoints((FakeEntryPoint("not_callable", "broken:value", object()),))
    monkeypatch.setattr(registry_module.importlib_metadata, "entry_points", lambda: entry_points)

    with pytest.raises(RuntimeError, match="failed to load onestep resource plugin 'not_callable'"):
        load_resource_plugins(ResourceRegistry())


@pytest.mark.parametrize("resource_type", ["feishu_bitable", "mysql", "rabbitmq", "redis", "sqs"])
def test_core_does_not_register_plugin_resources_without_plugins(monkeypatch, resource_type: str) -> None:
    monkeypatch.setattr(registry_module.importlib_metadata, "entry_points", lambda: FakeEntryPoints(()))

    with pytest.raises(ValueError, match=f"unsupported resource type '{resource_type}'"):
        validate_app_config(
            {
                "name": "core-only",
                "resources": {
                    "candidate": {
                        "type": resource_type,
                    },
                },
                "tasks": [],
            },
            registry=ResourceRegistry(),
        )


def test_plugin_resource_strict_allowed_fields_and_validate_callback() -> None:
    registry = ResourceRegistry()
    registry.register_resource_type(_BOX_HANDLER)

    validate_app_config(
        {
            "name": "plugin-strict",
            "resources": {
                "box": {
                    "type": "test_registry_box",
                    "value": "ok",
                }
            },
            "tasks": [],
        },
        registry=registry,
    )

    with pytest.raises(ValueError, match="unsupported fields for resources.box: extra"):
        validate_app_config(
            {
                "name": "plugin-strict",
                "resources": {
                    "box": {
                        "type": "test_registry_box",
                        "value": "ok",
                        "extra": True,
                    }
                },
                "tasks": [],
            },
            registry=registry,
        )

    with pytest.raises(ValueError, match="'value' must be a non-empty string"):
        validate_app_config(
            {
                "name": "plugin-strict",
                "resources": {
                    "box": {
                        "type": "test_registry_box",
                    }
                },
                "tasks": [],
            },
            registry=registry,
        )


def test_load_app_config_builds_registered_resource_with_dependency() -> None:
    register_resource_type(_DEPENDENCY_HANDLER)
    register_resource_type(_DEPENDENT_HANDLER)

    app = load_app_config(
        {
            "name": "plugin-build",
            "resources": {
                "base": {
                    "type": "test_registry_dependency",
                    "value": "root",
                },
                "child": {
                    "type": "test_registry_dependent",
                    "connector": "base",
                },
            },
            "tasks": [],
        },
        strict=True,
    )

    assert app.resources["base"] == {"name": "base", "value": "root"}
    assert app.resources["child"]["dependency"] is app.resources["base"]


def test_load_app_config_preserves_cycle_detection_for_registered_resources() -> None:
    register_resource_type(_DEPENDENT_HANDLER)

    with pytest.raises(ValueError, match="cyclic resource reference detected: left -> right -> left"):
        load_app_config(
            {
                "name": "plugin-cycle",
                "resources": {
                    "left": {
                        "type": "test_registry_dependent",
                        "connector": "right",
                    },
                    "right": {
                        "type": "test_registry_dependent",
                        "connector": "left",
                    },
                },
                "tasks": [],
            },
            strict=True,
        )
