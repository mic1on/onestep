from __future__ import annotations

import ast
from pathlib import Path

import pytest

from onestep.testing import ConnectorCapability as Capability
from onestep.testing import ConnectorConformanceProfile


PROFILES = (
    ConnectorConformanceProfile(
        name="clickhouse",
        contracts={
            Capability.ACKNOWLEDGED_SINK: (
                "plugins/onestep-clickhouse/tests/test_clickhouse_connector.py::test_runtime_ack_follows_clickhouse_insert_acknowledgement",
            ),
            Capability.CHUNKED_SINK: (
                "plugins/onestep-clickhouse/tests/test_clickhouse_connector.py::test_send_awaits_each_chunk_in_order",
            ),
            Capability.PUBLIC_ERRORS: (
                "plugins/onestep-clickhouse/tests/test_clickhouse_connector.py::test_send_failure_does_not_leak_dsn_credentials",
            ),
        },
    ),
    ConnectorConformanceProfile(
        name="elasticsearch",
        contracts={
            Capability.ACKNOWLEDGED_SINK: (
                "plugins/onestep-elasticsearch/tests/test_elasticsearch_connector.py::test_runtime_ack_follows_backend_bulk_acknowledgement",
            ),
            Capability.CHUNKED_SINK: (
                "plugins/onestep-elasticsearch/tests/test_elasticsearch_connector.py::test_send_waits_for_every_success_item",
            ),
            Capability.PUBLIC_ERRORS: (
                "plugins/onestep-elasticsearch/tests/test_elasticsearch_connector.py::test_transport_errors_redact_hosts_headers_and_generated_auth",
            ),
        },
    ),
    ConnectorConformanceProfile(
        name="feishu-bitable",
        contracts={
            Capability.BASIC_SOURCE: (
                "plugins/onestep-feishu-bitable/tests/test_feishu_bitable_connector.py::test_feishu_incremental_source_advances_cursor_in_ack_order_and_resumes",
            ),
            Capability.CHECKPOINT_SOURCE: (
                "plugins/onestep-feishu-bitable/tests/test_feishu_bitable_connector.py::test_feishu_incremental_source_advances_cursor_in_ack_order_and_resumes",
            ),
            Capability.ACKNOWLEDGED_SINK: (
                "plugins/onestep-feishu-bitable/tests/test_feishu_bitable_connector.py::test_feishu_connector_fetches_and_reuses_tenant_token_for_create_sink",
            ),
            Capability.REPLAY_SAFE_SINK: (
                "plugins/onestep-feishu-bitable/tests/test_feishu_bitable_connector.py::test_feishu_table_sink_upsert_creates_updates_and_rejects_duplicates",
            ),
            Capability.PUBLIC_ERRORS: (
                "plugins/onestep-feishu-bitable/tests/test_feishu_bitable_connector.py::test_feishu_descriptors_redact_app_token_and_omit_credentials",
                "plugins/onestep-feishu-bitable/tests/test_feishu_bitable_connector.py::test_feishu_http_error_classifies_rate_limit_as_throttled",
            ),
        },
    ),
    ConnectorConformanceProfile(
        name="kafka",
        contracts={
            Capability.BASIC_SOURCE: (
                "plugins/onestep-kafka/tests/test_kafka_connector.py::test_kafka_topic_fetch_decodes_envelopes_and_injects_metadata",
            ),
            Capability.CHECKPOINT_SOURCE: (
                "plugins/onestep-kafka/tests/test_kafka_connector.py::test_ack_commits_only_after_contiguous_offsets_complete",
            ),
            Capability.CLAIMED_SOURCE: (
                "plugins/onestep-kafka/tests/test_kafka_runtime_contract.py::test_stop_controls_release_fetched_unstarted_kafka_delivery",
            ),
            Capability.ACKNOWLEDGED_SINK: (
                "plugins/onestep-kafka/tests/test_kafka_runtime_contract.py::test_runtime_ack_follows_kafka_producer_acknowledgement",
            ),
            Capability.PUBLIC_ERRORS: (
                "plugins/onestep-kafka/tests/test_kafka_connector.py::test_kafka_topic_redacts_consumer_and_producer_passwords",
            ),
        },
    ),
    ConnectorConformanceProfile(
        name="mongodb",
        contracts={
            Capability.BASIC_SOURCE: (
                "plugins/onestep-mongodb/tests/test_mongodb_change_stream.py::test_default_watch_emits_complete_raw_delete_event",
            ),
            Capability.CHECKPOINT_SOURCE: (
                "plugins/onestep-mongodb/tests/test_mongodb_polling.py::test_polling_persists_only_the_contiguous_ack_prefix",
                "plugins/onestep-mongodb/tests/test_mongodb_change_stream.py::test_change_tokens_persist_only_after_contiguous_ack",
            ),
            Capability.CLAIMED_SOURCE: (
                "plugins/onestep-mongodb/tests/test_mongodb_runtime_contract.py::test_stop_controls_release_unstarted_without_committing",
            ),
            Capability.ACKNOWLEDGED_SINK: (
                "plugins/onestep-mongodb/tests/test_mongodb_runtime_contract.py::test_runtime_ack_follows_mongodb_write_acknowledgement",
            ),
            Capability.CHUNKED_SINK: (
                "plugins/onestep-mongodb/tests/test_mongodb_sink.py::test_insert_mapping_and_chunked_sequence",
            ),
            Capability.PUBLIC_ERRORS: (
                "plugins/onestep-mongodb/tests/test_mongodb_resilience.py::test_redacted_cause_does_not_retain_credentials_or_invalid_documents",
            ),
        },
    ),
    ConnectorConformanceProfile(
        name="sql",
        contracts={
            # The canonical onestep-sql package is the Phase 1 consolidation of
            # onestep-mysql + onestep-postgres; its conformance is the union of
            # both backends (issue #133, design PR #134). The legacy
            # onestep-mysql / onestep-postgres distributions became thin
            # forwarding shims in Phase 3 and no longer declare their own
            # resource entry points, so their conformance is owned here.
            Capability.BASIC_SOURCE: (
                "plugins/onestep-mysql/tests/test_mysql_table_queue.py::test_mysql_table_queue_round_trip",
                "plugins/onestep-postgres/tests/test_postgres_table_queue.py::test_postgres_table_queue_round_trip",
            ),
            Capability.CHECKPOINT_SOURCE: (
                "plugins/onestep-mysql/tests/test_mysql_incremental.py::test_mysql_incremental_cursor_advances_in_order",
                "plugins/onestep-mysql/tests/test_mysql_binlog.py::test_mysql_binlog_cursor_advances_in_order",
                "plugins/onestep-postgres/tests/test_postgres_incremental.py::test_postgres_incremental_cursor_advances_in_order",
            ),
            Capability.CLAIMED_SOURCE: (
                "plugins/onestep-mysql/tests/test_mysql_runtime_contract.py::test_table_queue_stop_controls_release_claimed_rows",
                "plugins/onestep-postgres/tests/test_postgres_runtime_contract.py::test_table_queue_stop_controls_release_claimed_rows",
            ),
            Capability.ACKNOWLEDGED_SINK: (
                "plugins/onestep-mysql/tests/test_mysql_table_queue.py::test_mysql_table_queue_round_trip",
                "plugins/onestep-postgres/tests/test_postgres_table_queue.py::test_postgres_table_queue_round_trip",
            ),
            Capability.PUBLIC_ERRORS: (
                "plugins/onestep-mysql/tests/test_mysql_plugin.py::test_mysql_connector_error_does_not_leak_dsn_credentials",
                "plugins/onestep-postgres/tests/test_postgres_plugin.py::test_postgres_connector_error_does_not_leak_dsn_credentials",
            ),
        },
    ),
    ConnectorConformanceProfile(
        name="rabbitmq",
        contracts={
            Capability.BASIC_SOURCE: (
                "plugins/onestep-rabbitmq/tests/test_rabbitmq_connector.py::test_rabbitmq_queue_send_fetch_retry_fail_and_exchange_binding",
            ),
            Capability.CHECKPOINT_SOURCE: (
                "plugins/onestep-rabbitmq/tests/test_rabbitmq_connector.py::test_rabbitmq_queue_send_fetch_retry_fail_and_exchange_binding",
            ),
            Capability.ACKNOWLEDGED_SINK: (
                "plugins/onestep-rabbitmq/tests/test_rabbitmq_connector.py::test_rabbitmq_queue_send_fetch_retry_fail_and_exchange_binding",
            ),
            Capability.PUBLIC_ERRORS: (
                "plugins/onestep-rabbitmq/tests/test_rabbitmq_connector.py::test_rabbitmq_open_failure_does_not_leak_url_credentials",
            ),
        },
    ),
    ConnectorConformanceProfile(
        name="redis",
        contracts={
            Capability.BASIC_SOURCE: (
                "plugins/onestep-redis/tests/test_redis_connector.py::TestRedisStreamQueue::test_send_and_fetch_mock",
            ),
            Capability.CHECKPOINT_SOURCE: (
                "plugins/onestep-redis/tests/test_redis_connector.py::TestRedisStreamQueue::test_send_and_fetch_mock",
            ),
            Capability.ACKNOWLEDGED_SINK: (
                "plugins/onestep-redis/tests/test_redis_connector.py::TestRedisStreamQueue::test_send_with_maxlen",
            ),
            Capability.PUBLIC_ERRORS: (
                "plugins/onestep-redis/tests/test_redis_resilience.py::test_send_failure_does_not_leak_url_credentials",
            ),
        },
    ),
    ConnectorConformanceProfile(
        name="sqs",
        contracts={
            Capability.BASIC_SOURCE: (
                "plugins/onestep-sqs/tests/test_sqs_connector.py::test_sqs_queue_send_fetch_batch_delete_and_fail_delete",
            ),
            Capability.CHECKPOINT_SOURCE: (
                "plugins/onestep-sqs/tests/test_sqs_connector.py::test_sqs_queue_send_fetch_batch_delete_and_fail_delete",
            ),
            Capability.ACKNOWLEDGED_SINK: (
                "plugins/onestep-sqs/tests/test_sqs_connector.py::test_sqs_queue_send_fetch_batch_delete_and_fail_delete",
            ),
            Capability.PUBLIC_ERRORS: (
                "plugins/onestep-sqs/tests/test_sqs_plugin.py::test_sqs_connector_error_does_not_leak_option_secrets",
            ),
        },
    ),
    ConnectorConformanceProfile(
        name="cf-queues",
        contracts={
            Capability.BASIC_SOURCE: (
                "plugins/onestep-cf-queues/tests/test_cf_queues_connector.py::test_cf_queue_pull_decodes_body_and_injects_metadata",
            ),
            Capability.CLAIMED_SOURCE: (
                "plugins/onestep-cf-queues/tests/test_cf_queues_runtime_contract.py::test_stop_controls_release_fetched_unstarted_cf_delivery",
            ),
            Capability.ACKNOWLEDGED_SINK: (
                "plugins/onestep-cf-queues/tests/test_cf_queues_runtime_contract.py::test_runtime_ack_follows_cf_queues_publish_acknowledgement",
            ),
            Capability.PUBLIC_ERRORS: (
                "plugins/onestep-cf-queues/tests/test_cf_queues_plugin.py::test_cf_queues_error_does_not_leak_api_token",
            ),
        },
    ),
)


REPO_ROOT = Path(__file__).parents[2]
RESOURCE_ENTRY_POINT = '[project.entry-points."onestep.resources"]'


def _official_connector_names() -> tuple[str, ...]:
    return tuple(
        sorted(
            pyproject.parent.name.removeprefix("onestep-")
            for pyproject in (REPO_ROOT / "plugins").glob("*/pyproject.toml")
            if RESOURCE_ENTRY_POINT in pyproject.read_text(encoding="utf-8")
        )
    )


def _find_contract_test(
    tree: ast.Module,
    qualname: str,
) -> tuple[ast.FunctionDef | ast.AsyncFunctionDef, ast.ClassDef | None] | None:
    parts = qualname.split("::")
    if len(parts) == 1:
        function = next(
            (
                node
                for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == parts[0]
            ),
            None,
        )
        return (function, None) if function is not None else None

    if len(parts) != 2:
        return None

    class_name, function_name = parts
    test_class = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == class_name
        ),
        None,
    )
    if test_class is None or not test_class.name.startswith("Test"):
        return None

    function = next(
        (
            node
            for node in test_class.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == function_name
        ),
        None,
    )
    return (function, test_class) if function is not None else None


def _decorator_name(decorator: ast.expr) -> str:
    if isinstance(decorator, ast.Call):
        return _decorator_name(decorator.func)
    if isinstance(decorator, ast.Attribute):
        prefix = _decorator_name(decorator.value)
        return f"{prefix}.{decorator.attr}" if prefix else decorator.attr
    if isinstance(decorator, ast.Name):
        return decorator.id
    return ""


def _has_skip_marker(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> bool:
    return any(
        _decorator_name(decorator).split(".")[-1] in {"skip", "skipif"}
        for decorator in node.decorator_list
    )


def test_every_official_connector_declares_a_profile() -> None:
    profile_names = tuple(profile.name for profile in PROFILES)
    assert len(profile_names) == len(set(profile_names)), "connector profile names must be unique"
    assert set(profile_names) == set(_official_connector_names())


def test_contract_lookup_requires_an_exact_collectable_qualname() -> None:
    tree = ast.parse(
        """
def test_top_level():
    pass

class TestContract:
    def test_method(self):
        pass

def helper():
    def test_nested():
        pass
"""
    )

    assert _find_contract_test(tree, "test_top_level") is not None
    assert _find_contract_test(tree, "TestContract::test_method") is not None
    assert _find_contract_test(tree, "WrongClass::test_method") is None
    assert _find_contract_test(tree, "test_nested") is None
    assert _find_contract_test(tree, "helper::test_nested") is None


@pytest.mark.parametrize(
    ("profile", "contract_id"),
    [
        (profile, contract_id)
        for profile in PROFILES
        for contract_ids in profile.contracts.values()
        for contract_id in contract_ids
    ],
    ids=lambda value: value.name if isinstance(value, ConnectorConformanceProfile) else value,
)
def test_official_profile_evidence_names_real_unit_contracts(
    profile: ConnectorConformanceProfile,
    contract_id: str,
) -> None:
    relative_path, separator, test_qualname = contract_id.partition("::")
    assert separator, f"{profile.name} contract must include a pytest-style node ID"
    assert "/integration/" not in relative_path, f"{profile.name} must have unit-level evidence"

    path = REPO_ROOT / relative_path
    assert path.is_file(), f"{profile.name} contract file does not exist: {relative_path}"

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    located = _find_contract_test(tree, test_qualname)
    assert located is not None, f"{profile.name} contract test does not exist: {contract_id}"

    function, test_class = located
    assert function.name.startswith("test_"), f"{profile.name} evidence is not a pytest test"
    assert not _has_skip_marker(function), f"{profile.name} evidence is marked skipped"
    assert test_class is None or not _has_skip_marker(
        test_class
    ), f"{profile.name} evidence class is marked skipped"
