"""Contract guard: every YAML connector snippet in README.md must stay valid.

The README carries a connector decision table plus a minimal ``resources:``
snippet per official connector (issue #154). Docs rot silently otherwise, so
this suite extracts every fenced `````yaml```` block from ``README.md`` and
replays each resources block through ``load_app_config(strict=True)`` — the
same strict validation ``onestep check --strict`` applies.

Snippets whose connector plugins are not installed in the current environment
are skipped (not failed) so the guard stays green in minimal test installs;
any other strict-validation error fails the suite. Placeholder snippets (the
``<source-resource>`` task pairing example) are skipped outright.
"""
from __future__ import annotations

import asyncio
import importlib.util
import re
from pathlib import Path

import pytest

from onestep.config import load_app_config

README = Path(__file__).resolve().parents[2] / "README.md"

# resource type -> providing importable module (used to detect environment gaps)
_PLUGIN_MODULES: dict[str, str] = {
    "redis": "onestep_redis",
    "rabbitmq": "onestep_rabbitmq",
    "sqs": "onestep_sqs",
    "sns": "onestep_sqs",
    "cf_queue": "onestep_cf_queues",
    "cf_queues": "onestep_cf_queues",
    "mysql": "onestep_sql",
    "mysql_binlog": "onestep_sql",
    "mysql_cursor_store": "onestep_sql",
    "mysql_incremental": "onestep_sql",
    "mysql_state_store": "onestep_sql",
    "mysql_table_queue": "onestep_sql",
    "mysql_table_sink": "onestep_sql",
    "postgres": "onestep_sql",
    "postgres_cursor_store": "onestep_sql",
    "postgres_execution_source": "onestep_sql",
    "postgres_incremental": "onestep_sql",
    "postgres_state_store": "onestep_sql",
    "postgres_table_queue": "onestep_sql",
    "postgres_table_sink": "onestep_sql",
    "kafka": "onestep_kafka",
    "kafka_topic": "onestep_kafka",
    "mongodb": "onestep_mongodb",
    "mongodb_polling": "onestep_mongodb",
    "mongodb_change_stream": "onestep_mongodb",
    "mongodb_collection_sink": "onestep_mongodb",
    "elasticsearch": "onestep_elasticsearch",
    "elasticsearch_bulk_sink": "onestep_elasticsearch",
    "clickhouse": "onestep_clickhouse",
    "clickhouse_table_sink": "onestep_clickhouse",
    "feishu_bitable": "onestep_feishu_bitable",
    "feishu_bitable_incremental": "onestep_feishu_bitable",
    "feishu_bitable_table_sink": "onestep_feishu_bitable",
}


def _fenced_yaml_blocks() -> list[tuple[int, str]]:
    text = README.read_text(encoding="utf-8")
    return [(index, block) for index, block in enumerate(re.findall(r"```yaml\n(.*?)```", text, re.DOTALL))]


def _resource_types(block: str) -> set[str]:
    types: set[str] = set()
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith("type:"):
            value = stripped[len("type:") :].strip().strip("\"'")
            if value:
                types.add(value)
    return types


def _missing_plugin_modules(block: str) -> set[str]:
    missing: set[str] = set()
    for resource_type in _resource_types(block):
        module = _PLUGIN_MODULES.get(resource_type)
        if module and importlib.util.find_spec(module) is None:
            missing.add(module)
    return missing


def _is_placeholder_snippet(block: str) -> bool:
    return "<" in block or "resources:" not in block


_blocks = _fenced_yaml_blocks()
_resource_blocks = [
    (index, block) for index, block in _blocks if not _is_placeholder_snippet(block)
]


def test_readme_contains_connector_snippets() -> None:
    # The decision-table work (issue #154) promises at least one runnable
    # resources block per official connector family; guard against the
    # snippets being dropped from the README silently.
    assert len(_resource_blocks) >= 10


@pytest.mark.parametrize(
    "index,block",
    _resource_blocks,
    ids=[f"readme-yaml-block-{index}" for index, _ in _resource_blocks],
)
def test_readme_yaml_resources_block_passes_strict_validation(index: int, block: str) -> None:
    missing = _missing_plugin_modules(block)
    if missing:
        pytest.skip(f"connector plugins not installed: {sorted(missing)}")

    # Some connectors create asyncio primitives (locks/queues) at construction
    # time. On Python 3.9 those bind the *current* event loop eagerly, and a
    # previous async test in the same process may have cleared it, so make
    # sure one exists before strict validation runs.
    try:
        asyncio.get_event_loop_policy().get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    import yaml

    parsed = yaml.safe_load(block)
    assert isinstance(parsed, dict) and "resources" in parsed, "snippet must declare resources:"

    config = {
        "apiVersion": "onestep/v1alpha1",
        "kind": "App",
        "app": {"name": f"readme-snippet-{index}"},
        "resources": parsed["resources"],
        "tasks": [],
    }
    load_app_config(config, strict=True)
