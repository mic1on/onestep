from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from importlib import metadata as importlib_metadata
from typing import Any
from uuid import UUID

from onestep import ControlPlaneReporter, ControlPlaneReporterConfig, OneStepApp
from onestep.config import load_app_config
from onestep.resilience import ConnectorErrorKind, ConnectorOperation, ConnectorOperationError
from onestep_redis import RedisConnector, RedisStreamQueue
from onestep_redis.resilience import as_redis_connector_operation_error, classify_redis_error


@dataclass
class SenderRecorder:
    calls: list[tuple[str, dict]] = field(default_factory=list)

    async def __call__(self, channel: str, payload: dict) -> None:
        self.calls.append((channel, payload))


def test_package_exposes_onestep_resource_entry_point() -> None:
    entry_points = _entry_points_for_group("onestep.resources")

    assert any(
        entry_point.name == "redis"
        and entry_point.value == "onestep_redis:register"
        for entry_point in entry_points
    )


def test_yaml_builds_redis_resources_via_plugin_entry_point() -> None:
    app = load_app_config(
        {
            "apiVersion": "onestep/v1alpha1",
            "kind": "App",
            "app": {
                "name": "redis-plugin",
            },
            "resources": {
                "redis": {
                    "type": "redis",
                    "url": "redis://localhost:6379",
                    "options": {"decode_responses": False},
                },
                "jobs": {
                    "type": "redis_stream",
                    "connector": "redis",
                    "stream": "jobs",
                    "group": "workers",
                    "consumer": "worker-a",
                    "batch_size": 25,
                    "poll_interval_s": 0.5,
                    "block_ms": 250,
                    "maxlen": 1000,
                    "approximate_trim": False,
                },
            },
            "tasks": [],
        },
        strict=True,
    )

    assert isinstance(app.resources["redis"], RedisConnector)
    assert app.resources["redis"].url == "redis://localhost:6379"
    assert app.resources["redis"].options == {"decode_responses": False}
    assert isinstance(app.resources["jobs"], RedisStreamQueue)
    assert app.resources["jobs"].connector is app.resources["redis"]
    assert app.resources["jobs"].name == "jobs"
    assert app.resources["jobs"].group == "workers"
    assert app.resources["jobs"].consumer == "worker-a"
    assert app.resources["jobs"].batch_size == 25
    assert app.resources["jobs"].block_ms == 250
    assert app.resources["jobs"].maxlen == 1000
    assert app.resources["jobs"].approximate_trim is False


def test_redis_plugin_normalizes_redis_errors() -> None:
    timeout = TimeoutError("timeout")

    assert classify_redis_error(timeout) is ConnectorErrorKind.DISCONNECTED
    normalized = as_redis_connector_operation_error(
        operation=ConnectorOperation.FETCH,
        exc=timeout,
        source_name="jobs",
        retry_delay_s=0.5,
    )

    assert isinstance(normalized, ConnectorOperationError)
    assert normalized.backend == "redis"
    assert normalized.operation is ConnectorOperation.FETCH
    assert normalized.kind is ConnectorErrorKind.DISCONNECTED
    assert normalized.source_name == "jobs"
    assert normalized.retry_delay_s == 0.5
    assert normalized.cause is timeout


def test_reporter_sync_payload_includes_redis_stream_topology_config() -> None:
    recorder = SenderRecorder()
    redis = RedisConnector("redis://localhost:6379")
    source = redis.stream(
        "jobs",
        group="workers",
        consumer="worker-a",
        batch_size=25,
        poll_interval_s=0.5,
        block_ms=250,
        maxlen=1000,
        approximate_trim=False,
    )
    app = OneStepApp("billing-sync")

    @app.task(source=source)
    async def sync_users(ctx, payload):
        return payload

    reporter = ControlPlaneReporter(_make_config(), sender=recorder)
    reporter.attach(app)

    async def scenario() -> None:
        await reporter.send_sync_now()

    asyncio.run(scenario())

    sync_payload = [payload for channel, payload in recorder.calls if channel == "sync"][0]
    assert sync_payload["app"]["tasks"][0]["source"] == {
        "kind": "redis_stream",
        "name": "jobs",
        "config": {
            "stream": "jobs",
            "group": "workers",
            "consumer": "worker-a",
            "batch_size": 25,
            "poll_interval_s": 0.5,
            "block_ms": 250,
            "start_id": "$",
            "create_group": True,
            "maxlen": 1000,
            "approximate_trim": False,
        },
    }


def _make_config() -> ControlPlaneReporterConfig:
    return ControlPlaneReporterConfig(
        base_url="https://control-plane.example.com",
        token="secret-token",
        environment="prod",
        service_name="billing-sync",
        node_name="vm-prod-3",
        deployment_version="1.0.0+c435c99",
        instance_id=UUID("8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df"),
        heartbeat_interval_s=3600.0,
        metrics_interval_s=3600.0,
        event_flush_interval_s=3600.0,
        event_batch_size=10,
        max_pending_events=100,
        max_pending_metric_batches=10,
        timeout_s=3.0,
        reconnect_base_delay_s=0.01,
        reconnect_max_delay_s=0.02,
    )


def _entry_points_for_group(group: str) -> tuple[Any, ...]:
    entry_points = importlib_metadata.entry_points()
    if hasattr(entry_points, "select"):
        return tuple(entry_points.select(group=group))
    return tuple(entry_points.get(group, ()))
