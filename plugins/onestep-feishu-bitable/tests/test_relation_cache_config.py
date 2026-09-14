from __future__ import annotations

from typing import Any

import pytest

from onestep.config import load_app_config
from onestep_feishu_bitable import FeishuBitableConnector

_RELATION_CACHE_POLICIES = ("none", "lazy", "eager")


def _make_sink(relation: dict[str, Any] | None = None):
    connector = FeishuBitableConnector(app_id="app-id", app_secret="secret")
    config = {"table_id": "companies", "key": "name"}
    config.update(relation or {})
    return connector.table_sink(
        app_token="project-app",
        table_id="projects",
        mode="create",
        relations={"companies": config},
    )


def test_relation_cache_defaults_to_none() -> None:
    sink = _make_sink()
    assert sink.relations[0].cache == "none"


@pytest.mark.parametrize("policy", _RELATION_CACHE_POLICIES)
def test_relation_cache_accepts_supported_policies(policy: str) -> None:
    sink = _make_sink({"cache": policy})
    assert sink.relations[0].cache == policy


def test_relation_cache_normalizes_case_like_on_missing() -> None:
    sink = _make_sink({"cache": " EAGER "})
    assert sink.relations[0].cache == "eager"


@pytest.mark.parametrize(
    "bad_value",
    ["always", "", "   ", True, 1, None, [], {}, "lazy-cache"],
)
def test_relation_cache_rejects_invalid_values(bad_value: Any) -> None:
    with pytest.raises((ValueError, TypeError)) as excinfo:
        _make_sink({"cache": bad_value})
    assert "relations.companies.cache" in str(excinfo.value)


def test_relation_cache_rejection_message_lists_supported_policies() -> None:
    with pytest.raises(ValueError, match=r"one of 'none', 'lazy', or 'eager'"):
        _make_sink({"cache": "always"})


def test_relation_cache_is_per_relation_field() -> None:
    connector = FeishuBitableConnector(app_id="app-id", app_secret="secret")
    sink = connector.table_sink(
        app_token="project-app",
        table_id="projects",
        mode="create",
        relations={
            "companies": {"table_id": "companies", "key": "name", "cache": "eager"},
            "depts": {"table_id": "depts", "key": "code", "cache": "lazy"},
            "people": {"table_id": "people", "key": "staff_id"},
        },
    )
    assert [relation.cache for relation in sink.relations] == ["eager", "lazy", "none"]


def _load_relations(relation_config: dict[str, Any]) -> Any:
    app = load_app_config(
        {
            "apiVersion": "onestep/v1alpha1",
            "kind": "App",
            "app": {"name": "relation-cache"},
            "resources": {
                "feishu": {"type": "feishu_bitable", "app_id": "id", "app_secret": "secret"},
                "sink": {
                    "type": "feishu_bitable_table_sink",
                    "connector": "feishu",
                    "app_token": "token",
                    "table_id": "projects",
                    "mode": "create",
                    "relations": {"companies": relation_config},
                },
            },
            "tasks": [],
        },
        strict=True,
    )
    return app.resources["sink"].relations


@pytest.mark.parametrize("policy", _RELATION_CACHE_POLICIES)
def test_yaml_strict_accepts_supported_cache_policies(policy: str) -> None:
    relations = _load_relations({"table_id": "companies", "key": "name", "cache": policy})
    assert relations[0].cache == policy


def test_yaml_strict_defaults_cache_to_none() -> None:
    relations = _load_relations({"table_id": "companies", "key": "name"})
    assert relations[0].cache == "none"


@pytest.mark.parametrize("bad_value", ["always", "", True, 1, ["lazy"]])
def test_yaml_strict_rejects_invalid_cache_policy(bad_value: Any) -> None:
    with pytest.raises((ValueError, TypeError)) as excinfo:
        _load_relations({"table_id": "companies", "key": "name", "cache": bad_value})
    assert "cache" in str(excinfo.value)


def test_descriptor_exposes_cache_without_secrets() -> None:
    connector = FeishuBitableConnector(app_id="app-id", app_secret="secret")
    sink = connector.table_sink(
        app_token="app-token-secret",
        table_id="projects",
        mode="create",
        relations={
            "companies": {
                "table_id": "companies",
                "key": "name",
                "app_token": "relation-token-secret",
                "cache": "eager",
            },
            "depts": {"table_id": "depts", "key": "code"},
        },
    )
    descriptor = sink.control_plane_descriptor()
    relations = descriptor["config"]["relations"]
    assert relations[0]["cache"] == "eager"
    assert relations[1]["cache"] == "none"
    assert "app-token-secret" not in str(descriptor)
    assert "relation-token-secret" not in str(descriptor)


def test_logger_name_and_public_api_unchanged() -> None:
    import onestep_feishu_bitable
    from onestep_feishu_bitable import _shared

    assert _shared._LOGGER_NAME == "onestep_feishu_bitable.connector"
    # Frozen at the pre-cache-change public surface: the cache feature must not
    # add or remove any public name.
    assert set(onestep_feishu_bitable.__all__) == {
        "FeishuBitableApiError",
        "FeishuBitableConnector",
        "FeishuBitableIncrementalSource",
        "FeishuBitablePayloadError",
        "FeishuBitableTableSink",
        "__version__",
        "feishu_bitable_text",
        "feishu_bitable_user",
        "register",
        "register_resources",
    }
