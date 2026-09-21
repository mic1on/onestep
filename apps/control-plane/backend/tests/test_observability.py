"""Tests for the process-local observability primitives (#197 baseline).

The assertions here are deliberately measurement-based rather than shape-based:
the pool histogram is compared against a wall-clock wait measured by the test
itself, and the event-loop sampler is compared against a deliberately blocked
loop. That is what makes "metrics agree with the actual wait" a checked claim.
"""

from __future__ import annotations

import ast
import asyncio
import logging
import re
import threading
import time
from pathlib import Path

import pytest
import sqlalchemy as sa
from onestep_control_plane_api.api.routers.prometheus import (
    PROMETHEUS_CONTENT_TYPE,
    build_observability_metrics,
    reset_prometheus_metrics_cache,
)
from onestep_control_plane_api.ops import observability as obs
from sqlalchemy.pool import QueuePool

IDENTITY_INSTANCE_ID = "3f2b6c1e-9d4a-4f18-8a5e-7c0d1e2f3a4b"
IDENTITY_SESSION_ID = "9c1d2e3f-4a5b-4c6d-8e9f-0a1b2c3d4e5f"
INGEST_TOKEN_VALUE = "super-secret-ingest-token-value"
AUTHORIZATION_VALUE = "Bearer super-secret-ingest-token-value"
MESSAGE_BODY_VALUE = "private-agent-message-body"


@pytest.fixture(autouse=True)
def _reset_observability() -> None:
    obs.reset_observability_state()
    reset_prometheus_metrics_cache()
    yield
    obs.reset_observability_state()
    reset_prometheus_metrics_cache()


def _file_engine(*, pool_size: int = 1, max_overflow: int = 0, tmp_path: Path):
    """Create a file-backed SQLite engine with a real (non-static) QueuePool."""

    return sa.create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'observability.db'}",
        connect_args={"check_same_thread": False},
        poolclass=QueuePool,
        pool_size=pool_size,
        max_overflow=max_overflow,
    )


# --------------------------------------------------------------------------------------
# Event-loop lag
# --------------------------------------------------------------------------------------


def test_event_loop_lag_sampler_measures_a_blocked_loop() -> None:
    sampler = obs.EventLoopLagSampler(interval_s=0.02, window_size=200)

    async def scenario() -> obs.EventLoopLagSnapshot:
        assert sampler.start() is True
        await asyncio.sleep(0.06)
        time.sleep(0.25)  # blocks the loop exactly like sync DB work would
        await asyncio.sleep(0.06)
        snapshot = sampler.snapshot()
        sampler.stop()
        return snapshot

    snapshot = asyncio.run(scenario())

    assert snapshot.sample_count >= 5
    assert snapshot.max_s is not None and snapshot.max_s >= 0.15
    assert snapshot.p95_s is not None and snapshot.p95_s >= 0.15
    assert snapshot.p50_s is not None and snapshot.p50_s < 0.15
    assert snapshot.latest_s is not None


def test_event_loop_lag_sampler_reports_percentiles_over_bounded_window() -> None:
    sampler = obs.EventLoopLagSampler(interval_s=0.5, window_size=5)
    for lag_s in (0.001, 0.002, 0.003, 0.004, 0.005, 0.006, 0.007):
        sampler.record_sample(lag_s)

    snapshot = sampler.snapshot()

    assert snapshot.window_size == 5
    assert snapshot.sample_count == 5
    assert snapshot.latest_s == 0.007
    assert snapshot.max_s == 0.007
    assert snapshot.p50_s == 0.005
    assert snapshot.p95_s == 0.007
    assert snapshot.running is False
    assert snapshot.last_sample_at is not None


def test_event_loop_lag_sampler_start_is_idempotent_and_stoppable() -> None:
    sampler = obs.EventLoopLagSampler(interval_s=0.02)

    async def scenario() -> tuple[bool, bool, bool]:
        first = sampler.start()
        second = sampler.start()
        running_before_stop = sampler.running
        sampler.stop()
        return first, second, running_before_stop

    first, second, running_before_stop = asyncio.run(scenario())

    assert (first, second) == (True, True)
    assert running_before_stop is True
    assert sampler.running is False


def test_event_loop_lag_sampler_start_without_running_loop_is_a_noop() -> None:
    sampler = obs.EventLoopLagSampler(interval_s=0.02)

    assert sampler.start() is False
    assert sampler.running is False
    assert sampler.snapshot().sample_count == 0


# --------------------------------------------------------------------------------------
# Connection-pool wait and occupancy
# --------------------------------------------------------------------------------------


def test_pool_wait_metric_agrees_with_measured_checkout_wait(tmp_path: Path) -> None:
    engine = _file_engine(tmp_path=tmp_path, pool_size=1, max_overflow=0)
    assert obs.instrument_engine(engine, name="test_pool") is True

    holder = engine.raw_connection()
    occupied = obs.refresh_pool_occupancy(engine, name="test_pool")
    assert occupied is not None
    assert occupied.checked_out == 1
    assert occupied.checked_in == 0
    assert occupied.overflow == 0
    assert occupied.size == 1

    def release() -> None:
        time.sleep(0.3)
        holder.close()

    releaser = threading.Thread(target=release)
    releaser.start()
    try:
        started = time.perf_counter()
        blocked = engine.raw_connection()
        measured_wait_s = time.perf_counter() - started
    finally:
        releaser.join()

    assert measured_wait_s >= 0.2

    snapshot = obs.collect_prometheus_snapshot()
    histogram = next(
        sample
        for sample in snapshot.histograms
        if sample.name == "onestep_control_plane_db_pool_wait_seconds"
    )

    assert histogram.count == 2
    assert histogram.labels == (("name", "test_pool"), ("pool", "QueuePool"))
    assert abs(histogram.total - measured_wait_s) < 0.05
    bucket_counts = dict(histogram.buckets)
    assert bucket_counts[0.05] == 1
    assert bucket_counts[float("inf")] == 2

    counter = next(
        sample
        for sample in snapshot.counters
        if sample.name == "onestep_control_plane_db_pool_checkouts_total"
    )
    assert counter.value == histogram.count

    slow_counter = next(
        sample
        for sample in snapshot.counters
        if sample.name == "onestep_control_plane_db_pool_wait_slow_total"
    )
    assert slow_counter.value == 1

    blocked.close()
    engine.dispose()


def test_pool_occupancy_gauges_agree_with_pool_state(tmp_path: Path) -> None:
    engine = _file_engine(tmp_path=tmp_path, pool_size=2, max_overflow=1)
    assert obs.instrument_engine(engine, name="occupancy_pool") is True

    first = engine.raw_connection()
    second = engine.raw_connection()
    third = engine.raw_connection()  # uses the single overflow slot
    snapshot = obs.refresh_pool_occupancy(engine, name="occupancy_pool")

    assert snapshot is not None
    pool = engine.pool
    assert snapshot.checked_out == pool.checkedout() == 3
    assert snapshot.checked_in == pool.checkedin()
    assert snapshot.overflow == pool.overflow() == 1
    assert snapshot.size == pool.size() == 2

    exported = {
        sample.name: sample.value
        for sample in obs.collect_prometheus_snapshot().gauges
        if sample.labels == (("name", "occupancy_pool"), ("pool", "QueuePool"))
    }
    assert exported["onestep_control_plane_db_pool_checked_out"] == float(pool.checkedout())
    assert exported["onestep_control_plane_db_pool_overflow"] == float(pool.overflow())
    assert exported["onestep_control_plane_db_pool_size"] == float(pool.size())
    assert exported["onestep_control_plane_db_pool_checked_in"] == float(pool.checkedin())

    for connection in (first, second, third):
        connection.close()
    idle = obs.refresh_pool_occupancy(engine, name="occupancy_pool")
    assert idle is not None
    assert idle.checked_out == 0
    engine.dispose()


def test_pool_occupancy_is_skipped_for_pools_without_accessors(tmp_path: Path) -> None:
    from sqlalchemy.pool import StaticPool

    engine = sa.create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    assert obs.refresh_pool_occupancy(engine) is None
    assert obs.instrument_engine(engine) is True
    engine.dispose()


def test_record_pool_wait_keeps_labels_bounded() -> None:
    obs.record_pool_wait(0.02, pool_class="QueuePool", pool_name="Bad Name!!")
    obs.record_pool_wait(0.02, pool_class="QueuePool", pool_name="ok_pool")

    labels = {
        sample.labels
        for sample in obs.collect_prometheus_snapshot().histograms
        if sample.name == "onestep_control_plane_db_pool_wait_seconds"
    }

    assert (("name", "unnamed"), ("pool", "QueuePool")) in labels
    assert (("name", "ok_pool"), ("pool", "QueuePool")) in labels


def test_instrument_engine_is_idempotent(tmp_path: Path) -> None:
    engine = _file_engine(tmp_path=tmp_path)

    assert obs.instrument_engine(engine, name="twice") is True
    assert obs.instrument_engine(engine, name="twice") is True

    connection = engine.raw_connection()
    connection.close()

    histogram = next(
        sample
        for sample in obs.collect_prometheus_snapshot().histograms
        if sample.name == "onestep_control_plane_db_pool_wait_seconds"
    )
    assert histogram.count == 1
    engine.dispose()


# --------------------------------------------------------------------------------------
# Scan duration
# --------------------------------------------------------------------------------------


def test_scan_duration_timer_records_duration_and_bounded_label() -> None:
    with obs.scan_duration_timer("notification_missed_start") as timing:
        time.sleep(0.05)

    assert timing.duration_s is not None and timing.duration_s >= 0.05
    assert timing.outcome == "ok"
    assert timing.finished_at is not None

    with obs.scan_duration_timer("not_a_registered_scan"):
        pass

    snapshot = obs.collect_prometheus_snapshot()
    histogram = next(
        sample
        for sample in snapshot.histograms
        if sample.name == "onestep_control_plane_scan_duration_seconds"
    )
    assert histogram.count == 1
    assert histogram.labels == (("scan", "notification_missed_start"),)

    other = [
        sample
        for sample in snapshot.histograms
        if sample.name == "onestep_control_plane_scan_duration_seconds"
        and sample.labels == (("scan", "other"),)
    ]
    assert len(other) == 1

    runs = {
        sample.labels: sample.value
        for sample in snapshot.counters
        if sample.name == "onestep_control_plane_scan_runs_total"
    }
    assert runs[(("scan", "notification_missed_start"),)] == 1
    assert runs[(("scan", "other"),)] == 1


def test_scan_duration_timer_records_failures_and_reraises() -> None:
    with pytest.raises(RuntimeError):
        with obs.scan_duration_timer("notification_outbox") as timing:
            raise RuntimeError("scan exploded")

    assert timing.outcome == "error"
    failures = {
        sample.labels: sample.value
        for sample in obs.collect_prometheus_snapshot().counters
        if sample.name == "onestep_control_plane_scan_failures_total"
    }
    assert failures[(("scan", "notification_outbox"),)] == 1


def test_async_scan_duration_timer_records_duration() -> None:
    async def scenario() -> obs.ScanTiming:
        async with obs.ascan_duration_timer("notification_outbox") as timing:
            await asyncio.sleep(0.05)
        return timing

    timing = asyncio.run(scenario())

    assert timing.duration_s is not None and timing.duration_s >= 0.05
    histogram = next(
        sample
        for sample in obs.collect_prometheus_snapshot().histograms
        if sample.name == "onestep_control_plane_scan_duration_seconds"
    )
    assert histogram.count == 1
    assert histogram.labels == (("scan", "notification_outbox"),)


def test_register_scan_name_extends_bounded_allowlist() -> None:
    assert obs.register_scan_name("custom_scan") == "custom_scan"
    assert obs.normalize_scan_name("custom_scan") == "custom_scan"
    assert obs.register_scan_name("Not A Name") == "other"


# --------------------------------------------------------------------------------------
# Privacy: credentials in logs, identity never in metric labels
# --------------------------------------------------------------------------------------


class _CapturingHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture()
def captured_logs() -> tuple[logging.Logger, _CapturingHandler]:
    capture_logger = logging.getLogger("onestep_control_plane_api.observability.test")
    capture_logger.setLevel(logging.DEBUG)
    capture_logger.propagate = False
    handler = _CapturingHandler()
    capture_logger.addHandler(handler)
    yield capture_logger, handler
    capture_logger.removeHandler(handler)


def test_no_credential_or_auth_header_value_can_appear_in_log_output(
    captured_logs: tuple[logging.Logger, _CapturingHandler],
) -> None:
    capture_logger, handler = captured_logs

    obs.log_ws_lifecycle(
        capture_logger,
        "disconnected",
        instance_id=IDENTITY_INSTANCE_ID,
        session_id=IDENTITY_SESSION_ID,
        close_code=1006,
        close_reason=f"auth {AUTHORIZATION_VALUE} token={INGEST_TOKEN_VALUE}",
        authorization=AUTHORIZATION_VALUE,
        ingest_token=INGEST_TOKEN_VALUE,
        message_body=MESSAGE_BODY_VALUE,
        payload={"raw_message": MESSAGE_BODY_VALUE, "token": INGEST_TOKEN_VALUE},
    )
    obs.emit_structured_log(
        capture_logger,
        logging.WARNING,
        "db_pool_wait_slow",
        wait_s=0.4,
        headers={"Authorization": AUTHORIZATION_VALUE},
        connector_secret=INGEST_TOKEN_VALUE,
        database_url=f"postgresql://user:{INGEST_TOKEN_VALUE}@localhost:5432/db",
    )

    assert len(handler.records) == 2
    rendered = "\n".join(
        f"{record.getMessage()} {record.__dict__}" for record in handler.records
    )

    for secret in (
        INGEST_TOKEN_VALUE,
        AUTHORIZATION_VALUE,
        MESSAGE_BODY_VALUE,
        "postgresql://user",
    ):
        assert secret not in rendered
    assert obs.REDACTED in rendered


def test_structured_log_records_carry_consistent_utc_timestamps(
    captured_logs: tuple[logging.Logger, _CapturingHandler],
) -> None:
    capture_logger, handler = captured_logs

    payload = obs.emit_structured_log(capture_logger, logging.INFO, "scan_run_slow", scan="other")

    assert payload["event"] == "scan_run_slow"
    assert payload["logged_at"] == handler.records[0].logged_at
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", payload["logged_at"])
    assert payload["logged_at"].endswith("+00:00")


def test_identity_is_used_for_log_correlation_only(
    captured_logs: tuple[logging.Logger, _CapturingHandler],
) -> None:
    capture_logger, handler = captured_logs

    payload = obs.log_ws_lifecycle(
        capture_logger,
        "connected",
        instance_id=IDENTITY_INSTANCE_ID,
        session_id=IDENTITY_SESSION_ID,
    )

    # Present in the log record (correlation) ...
    assert payload["instance_id"] == IDENTITY_INSTANCE_ID
    assert payload["session_id"] == IDENTITY_SESSION_ID
    assert handler.records[0].instance_id == IDENTITY_INSTANCE_ID
    assert handler.records[0].session_id == IDENTITY_SESSION_ID

    # ... and absent from every metric label and value in the exposition.
    obs.record_pool_wait(0.02, pool_class="QueuePool", pool_name="identity_check")
    with obs.scan_duration_timer("notification_missed_start"):
        pass
    body = build_observability_metrics()

    assert body
    assert IDENTITY_INSTANCE_ID not in body
    assert IDENTITY_SESSION_ID not in body
    assert "instance_id" not in body
    assert "session_id" not in body


def test_unknown_ws_close_reason_is_marked_unknown(
    captured_logs: tuple[logging.Logger, _CapturingHandler],
) -> None:
    capture_logger, _ = captured_logs

    payload = obs.log_ws_lifecycle(
        capture_logger,
        "disconnected",
        instance_id=IDENTITY_INSTANCE_ID,
        session_id=IDENTITY_SESSION_ID,
    )

    assert payload["close_code"] == "unknown"
    assert payload["close_code_known"] is False
    assert payload["close_reason"] == "unknown"
    assert payload["close_reason_known"] is False


# --------------------------------------------------------------------------------------
# Prometheus exposure through the existing exporter
# --------------------------------------------------------------------------------------


def test_observability_metrics_are_exposed_through_prometheus_endpoint(
    client,
    auth_headers,
) -> None:
    obs.record_pool_wait(0.02, pool_class="QueuePool", pool_name="endpoint_pool")
    with obs.scan_duration_timer("notification_missed_start"):
        pass

    response = client.get("/metrics", headers=auth_headers)

    assert response.status_code == 200
    assert response.headers["content-type"] == PROMETHEUS_CONTENT_TYPE
    body = response.text
    assert "# TYPE onestep_control_plane_db_pool_wait_seconds histogram" in body
    assert (
        'onestep_control_plane_db_pool_wait_seconds_count'
        '{name="endpoint_pool",pool="QueuePool"} 1'
    ) in body
    assert (
        'onestep_control_plane_scan_duration_seconds_count'
        '{scan="notification_missed_start"} 1'
    ) in body
    assert "onestep_control_plane_event_loop_lag_sample_interval_seconds 0.25" in body
    assert "onestep_control_plane_event_loop_lag_sampler_running 1" in body
    assert IDENTITY_INSTANCE_ID not in body
    assert IDENTITY_SESSION_ID not in body


def test_observability_metrics_are_not_exposed_without_auth(client) -> None:
    obs.record_pool_wait(0.02, pool_class="QueuePool", pool_name="secret_pool")

    response = client.get("/metrics")

    assert response.status_code == 401
    assert "onestep_control_plane_db_pool_wait_seconds" not in response.text


def test_build_observability_metrics_has_no_database_access(db_session) -> None:
    statement_count = 0

    def count_statements(*_args: object) -> None:
        nonlocal statement_count
        statement_count += 1

    engine = db_session.get_bind()
    sa.event.listen(engine, "before_cursor_execute", count_statements)
    try:
        body = build_observability_metrics()
    finally:
        sa.event.remove(engine, "before_cursor_execute", count_statements)

    assert "onestep_control_plane_event_loop_lag_sample_interval_seconds" in body
    assert statement_count == 0


def test_observability_section_is_not_frozen_by_the_database_cache(
    client,
    auth_headers,
) -> None:
    first = client.get("/metrics", headers=auth_headers).text
    obs.record_pool_wait(0.03, pool_class="QueuePool", pool_name="fresh_pool")
    second = client.get("/metrics", headers=auth_headers).text

    assert "fresh_pool" not in first
    expected = (
        'onestep_control_plane_db_pool_checkouts_total{name="fresh_pool",pool="QueuePool"} 1'
    )
    assert expected in second


# --------------------------------------------------------------------------------------
# Structural constraints from the #197 contract
# --------------------------------------------------------------------------------------


def test_module_does_not_import_modules_that_do_not_exist_on_main() -> None:
    """The baseline must import cleanly today, so no #192/#193 module may be imported."""

    module_path = Path(obs.__file__)
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    forbidden_fragments = (
        "agent_ws",
        "notification_scanner",
        "db.session",
        "core.settings",
        "worker_agent_ws",
    )
    offenders = [
        name
        for name in imported
        if any(fragment in name for fragment in forbidden_fragments)
    ]

    assert offenders == []
    assert imported <= {
        "__future__",
        "asyncio",
        "logging",
        "math",
        "re",
        "threading",
        "time",
        "bisect",
        "collections",
        "collections.abc",
        "contextlib",
        "dataclasses",
        "datetime",
        "typing",
        "uuid",
    }


def test_module_documents_units_sampling_and_overhead() -> None:
    docstring = obs.__doc__ or ""

    for required in (
        "Unit",
        "Sampling strategy",
        "Per-measurement overhead",
        "seconds",
        "Cardinality and privacy contract",
        "blind spot",
    ):
        assert required.lower() in docstring.lower()


def test_no_docker_or_deployment_paths_are_touched() -> None:
    """The baseline must not change Docker log retention or production deployment.

    The module is process-local instrumentation only: it must not read or write any
    Docker/compose/deployment configuration, and the compose files it must leave
    alone must still be present and untouched by this module.
    """

    worktree_root = Path(obs.__file__).resolve().parents[6]
    for relative in (
        "apps/control-plane/docker-compose.yml",
        "apps/control-plane/docker-compose.deploy.yml",
        "apps/control-plane/Dockerfile",
    ):
        assert (worktree_root / relative).exists()

    module_source = Path(obs.__file__).read_text(encoding="utf-8")
    for forbidden in ("docker", "compose", "log_retention", "logging.driver", "max-size"):
        assert forbidden not in module_source.lower()


def test_exposition_parses_as_prometheus_text_format() -> None:
    """Every emitted line must be valid Prometheus 0.0.4 text exposition."""

    obs.record_pool_wait(0.004, pool_class="QueuePool", pool_name="format_pool")
    with obs.scan_duration_timer("notification_missed_start"):
        pass
    obs.get_event_loop_lag_sampler().record_sample(0.01)

    lines = build_observability_metrics().splitlines()
    assert lines

    sample_pattern = re.compile(
        r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)"
        r"(\{(?P<labels>[^}]*)\})?"
        r" (?P<value>-?(?:[0-9]+(?:\.[0-9]+)?(?:[eE][-+]?[0-9]+)?|Inf|NaN))$"
    )
    label_pattern = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*="[^"]*"$')
    families: dict[str, str] = {}
    samples = 0

    for line in lines:
        if line.startswith("# HELP "):
            _, _, name, help_text = line.split(" ", 3)
            assert help_text, line
            families[name] = "help"
            continue
        if line.startswith("# TYPE "):
            _, _, name, metric_type = line.split(" ", 3)
            assert metric_type in {"counter", "gauge", "histogram", "summary", "untyped"}, line
            assert families.get(name) == "help", f"TYPE without HELP for {name}"
            families[name] = metric_type
            continue
        assert not line.startswith("#"), line
        match = sample_pattern.match(line)
        assert match is not None, line
        sample_name = match.group("name")
        family = families.get(sample_name)
        if family is None:
            # histogram samples carry the _bucket/_sum/_count suffixes
            for suffix in ("_bucket", "_sum", "_count"):
                if sample_name.endswith(suffix):
                    family = families.get(sample_name[: -len(suffix)])
                    break
        assert family in {"counter", "gauge", "histogram"}, (
            f"sample without a declared family: {line}"
        )
        labels = match.group("labels")
        if labels:
            for pair in labels.split(","):
                assert label_pattern.match(pair), pair
        samples += 1

    assert samples > 0
    assert "onestep_control_plane_db_pool_wait_seconds" in families


def test_label_cardinality_stays_bounded_under_identity_flood() -> None:
    """Many distinct pool/scan names must not create unbounded label series."""

    for index in range(200):
        obs.record_pool_wait(0.001, pool_class="QueuePool", pool_name=f"pool_{index}")
        with obs.scan_duration_timer(f"scan_{index}"):
            pass

    body = build_observability_metrics()
    pool_names = set(
        re.findall(
            r'onestep_control_plane_db_pool_wait_seconds_count\{[^}]*name="([^"]+)"',
            body,
        )
    )
    scan_names = set(
        re.findall(r'onestep_control_plane_scan_duration_seconds_count\{scan="([^"]+)"}', body)
    )

    # Pool names are caller-supplied and bounded in shape, so they may appear ...
    assert len(pool_names) == 200
    # ... but unregistered scan names all collapse onto the single "other" label.
    assert scan_names == {"other"}
    assert 'scan="scan_7"' not in body
    # No identity-shaped value leaks into any label.
    assert not re.search(r'(instance_id|session_id)="', body)


# --------------------------------------------------------------------------------------
# Credential redaction: nesting depth, cycles and robustness (#197 follow-up)
#
# The original redaction test only exercised Bearer-shaped values at the top
# level, so a bare secret under a sensitive key name -- which has no credential
# *shape* for the text scrubber to recognise -- shipped unredacted as soon as it
# was nested past the old two-level recursion guard. These tests pin the depth
# dimension and the two failure modes that motivated the bounded budget: a
# reference cycle and a pathologically deep structure. Both must be sanitized
# WITHOUT raising, because _sanitize_log_value runs before logger.log(...) and
# therefore propagates exceptions to the caller rather than to logging's
# handler error path.
# --------------------------------------------------------------------------------------

# A bare secret: no "Bearer " prefix, no "token=" assignment, so only the KEY
# NAME can identify it. This is the shape the original code leaked.
BARE_SECRET = "sk-bare-EXFILTRATED-9f8a7b6c5d4e"
SENSITIVE_LEAF_KEYS = ("token", "authorization", "password", "auth_header", "secret", "credential")

# The field the #197 wiring (t17) actually passes; kept non-sensitive so it does
# not mask the depth behaviour under test the way a sensitive top-level key would.
NEUTRAL_TOP_KEY = "ctx"


def _nest_secret(leaf_key: str, depth: int) -> dict[str, object]:
    """Return {NEUTRAL_TOP_KEY: {...{leaf_key: BARE_SECRET}...}} at `depth` levels."""

    value: object = {leaf_key: BARE_SECRET}
    for level in range(depth):
        value = {f"level_{level}": value}
    return {NEUTRAL_TOP_KEY: value}


@pytest.mark.parametrize("leaf_key", SENSITIVE_LEAF_KEYS)
@pytest.mark.parametrize("depth", [0, 1, 2, 3, 4, 5, 6, 7])
def test_bare_secret_under_sensitive_name_is_redacted_at_every_depth(
    leaf_key: str, depth: int
) -> None:
    """A bare secret must never survive, at any nesting depth.

    Fails against the pre-fix implementation (d920657) for depth >= 2: there the
    recursion stopped at `depth < 2` and the leftover subtree was stringified
    with `str(value)`, which prints the secret verbatim.
    """

    payload = obs.build_log_fields("probe", **{NEUTRAL_TOP_KEY: _nest_secret(leaf_key, depth)})

    rendered = repr(payload)
    assert BARE_SECRET not in rendered, f"leaked {leaf_key} at depth {depth}: {rendered}"
    assert obs.REDACTED in rendered


def test_bare_secret_is_redacted_beyond_the_depth_budget() -> None:
    """Past MAX_SANITIZE_DEPTH the subtree is redacted wholesale, not stringified."""

    budget = obs.MAX_SANITIZE_DEPTH
    for depth in (budget - 1, budget, budget + 1, budget * 2):
        payload = obs.build_log_fields("probe", **_nest_secret("token", depth))
        assert BARE_SECRET not in repr(payload), f"leaked at depth {depth}"
        assert obs.REDACTED in repr(payload), f"no placeholder at depth {depth}"


def test_bare_secret_in_nested_list_is_redacted() -> None:
    """The sequence branch must sanitize items, not just mappings."""

    payload = obs.build_log_fields(
        "probe", **{NEUTRAL_TOP_KEY: {"entries": [{"n": [{"token": BARE_SECRET}]}]}}
    )

    assert BARE_SECRET not in repr(payload)
    assert obs.REDACTED in repr(payload)


def test_cyclic_structure_is_sanitized_without_raising() -> None:
    """A self-referential dict must terminate and must not emit its secret.

    This is the caller-safety half of the fix: an unbounded walk would raise
    RecursionError, and because sanitizing happens before logger.log(...) that
    exception would reach the caller -- plausibly inside WS disconnect cleanup.
    """

    cyclic: dict[str, object] = {"name": "outer", "token": BARE_SECRET}
    cyclic["self"] = cyclic

    payload = obs.build_log_fields("probe", **{NEUTRAL_TOP_KEY: cyclic})

    assert BARE_SECRET not in repr(payload)
    assert obs.REDACTED in repr(payload)


def test_deeply_nested_structure_is_sanitized_without_raising() -> None:
    """A >=1000-level structure must not raise and must not emit its secret.

    Reachable in practice: json.loads parses arbitrarily deep input iteratively,
    so a deeply nested payload body can reach the sanitizer.
    """

    deep: object = {"token": BARE_SECRET}
    for _ in range(1200):
        deep = {"nested": deep}

    payload = obs.build_log_fields("probe", **{NEUTRAL_TOP_KEY: deep})

    assert BARE_SECRET not in repr(payload)
    assert obs.REDACTED in repr(payload)


def test_sanitizer_never_raises_on_adversarial_containers() -> None:
    """Property-style sweep: no input shape may raise out of the sanitizer."""

    cyclic_list: list[object] = [{"token": BARE_SECRET}]
    cyclic_list.append(cyclic_list)
    shared = {"token": BARE_SECRET}
    inputs: list[object] = [
        cyclic_list,
        {"a": shared, "b": shared},  # shared (not cyclic) subtree, repeated
        [[[[{"authorization": BARE_SECRET}]]]],
        {"tuple": ({"password": BARE_SECRET},)},
    ]
    for index, value in enumerate(inputs):
        payload = obs.build_log_fields("probe", **{NEUTRAL_TOP_KEY: value})
        assert BARE_SECRET not in repr(payload), f"leaked for input {index}"


def test_non_sensitive_name_is_documented_as_passing_through() -> None:
    """Pin the honest limitation: a bare secret under a NEUTRAL name is not caught.

    This locks the documented blind spot into a test so the docstring and runbook
    cannot drift back into overclaiming. If a future change starts catching this,
    the documentation must be updated together with this test.
    """

    payload = obs.build_log_fields("probe", note=BARE_SECRET)

    assert payload["note"] == BARE_SECRET
    assert "non-sensitive" in " ".join(_runbook_blind_spot_text().lower().split())


def _runbook_blind_spot_text() -> str:
    """Return the runbook's redaction blind-spot paragraph (or '' if absent)."""

    runbook = (
        Path(__file__).resolve().parents[2]
        / "docs"
        / "runbooks"
        / "control-plane-latency-diagnostics.md"
    )
    return runbook.read_text(encoding="utf-8")


def test_wiring_shape_payload_body_headers_authorization_is_redacted() -> None:
    """The planned t17 wiring shape must stay redacted after this fix.

    Guard against the repair regressing the usage it was written for: the wiring
    passes the raw message body through, whose headers carry the auth value.
    """

    payload = obs.build_log_fields(
        "probe", **{NEUTRAL_TOP_KEY: {"body": {"headers": {"authorization": BARE_SECRET}}}}
    )

    assert BARE_SECRET not in repr(payload)
    assert obs.REDACTED in repr(payload)


@pytest.mark.parametrize("marker", ["close_code_known", "close_reason_known"])
# close_code / close_reason themselves are keyword-only parameters, so a
# duplicate is a TypeError at call time; the *_known markers are the ones
# reachable through **fields and therefore need the runtime guard.

def test_caller_cannot_forge_ws_close_markers(marker: str) -> None:
    """A caller passing **fields must not be able to overwrite a derived marker.

    The markers state whether the close code/reason was genuinely known; letting
    a caller set close_reason_known=True next to close_reason="unknown" would
    report a missing reason as a confirmed one, undercutting the issue's
    "record unknown honestly" requirement.
    """

    payload = obs.log_ws_lifecycle(
        logging.getLogger("onestep_control_plane_api.observability.forge"),
        "disconnected",
        instance_id=IDENTITY_INSTANCE_ID,
        session_id=IDENTITY_SESSION_ID,
        close_code=None,
        close_reason=None,
        **{marker: True},
    )

    # Both markers must reflect the real (absent) code/reason...
    assert payload["close_code_known"] is False
    assert payload["close_reason_known"] is False
    assert payload["close_code"] == "unknown"
    assert payload["close_reason"] == "unknown"


def test_ws_close_markers_are_honest_when_values_are_known() -> None:
    """The positive counterpart: real values still produce known=True."""

    payload = obs.log_ws_lifecycle(
        logging.getLogger("onestep_control_plane_api.observability.forge"),
        "disconnected",
        instance_id=IDENTITY_INSTANCE_ID,
        session_id=IDENTITY_SESSION_ID,
        close_code=1006,
        close_reason="going away",
        close_reason_known=False,  # attempted forgery of the other direction
    )

    assert payload["close_code"] == 1006
    assert payload["close_code_known"] is True
    assert payload["close_reason"] == "going away"
    assert payload["close_reason_known"] is True
