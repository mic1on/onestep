from __future__ import annotations

import re
from typing import Any

import pytest

from onestep.config import load_app_config
from onestep_feishu_bitable import FeishuBitableConnector


def test_feishu_insert_key_index_config_normalizes_python_api() -> None:
    connector = FeishuBitableConnector(app_id="app-id", app_secret="secret")
    sink = connector.table_sink(
        app_token="app-token",
        table_id="tbl",
        mode="insert",
        match_fields=["编号"],
        batch_size=100,
        insert_key_index=True,
        insert_index_page_size=500,
        insert_index_max_pages=200,
        ambiguous_write_max_rounds=3,
    )
    assert sink.insert_key_index is True
    assert sink.insert_index_page_size == 500
    assert sink.insert_index_max_pages == 200
    assert sink.ambiguous_write_max_rounds == 3


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"mode": "upsert"}, r"insert_key_index.*mode.*insert"),
        ({"match_fields": ["编号", "来源"]}, r"exactly one match field"),
        ({"insert_index_page_size": 0}, r"insert_index_page_size.*[>=1]"),
        ({"insert_index_max_pages": 0}, r"insert_index_max_pages.*[>=1]"),
        ({"ambiguous_write_max_rounds": 0}, r"ambiguous_write_max_rounds.*[>=1]"),
    ],
)
def test_feishu_insert_key_index_rejects_unsupported_config(
    overrides: dict[str, object], message: str
) -> None:
    connector = FeishuBitableConnector(app_id="app-id", app_secret="secret")
    config: dict[str, object] = {
        "app_token": "app-token",
        "table_id": "tbl",
        "mode": "insert",
        "match_fields": ["编号"],
        "insert_key_index": True,
    }
    config.update(overrides)
    with pytest.raises((TypeError, ValueError), match=message):
        connector.table_sink(**config)


def test_feishu_insert_key_index_allows_relations() -> None:
    """insert_key_index and relations may now coexist (indexed insert resolves
    relations via the relation cache before writing)."""
    connector = FeishuBitableConnector(app_id="app-id", app_secret="secret")
    sink = connector.table_sink(
        app_token="app-token",
        table_id="tbl",
        mode="insert",
        match_fields=["编号"],
        insert_key_index=True,
        relations={
            "关联企业": {
                "from": "企业名称",
                "table_id": "enterprise",
                "key": "企业名称",
                "on_missing": "empty",
                "cache": "eager",
            }
        },
    )
    assert sink.insert_key_index is True
    assert len(sink.relations) == 1
    assert sink.relations[0].cache == "eager"


def test_yaml_builds_indexed_insert_sink_in_strict_mode() -> None:
    app = load_app_config(
        {
            "apiVersion": "onestep/v1alpha1",
            "kind": "App",
            "app": {"name": "follow-record-sync"},
            "resources": {
                "feishu": {
                    "type": "feishu_bitable",
                    "app_id": "id",
                    "app_secret": "secret",
                },
                "sink": {
                    "type": "feishu_bitable_table_sink",
                    "connector": "feishu",
                    "app_token": "token",
                    "table_id": "table",
                    "mode": "insert",
                    "match_fields": ["编号"],
                    "batch_size": 100,
                    "insert_key_index": True,
                    "insert_index_page_size": 500,
                    "insert_index_max_pages": 200,
                    "ambiguous_write_max_rounds": 3,
                },
            },
            "tasks": [],
        },
        strict=True,
    )
    assert app.resources["sink"].insert_key_index is True