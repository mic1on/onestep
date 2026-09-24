from __future__ import annotations

import asyncio

import sqlalchemy as sa
from onestep_control_plane_api.api.agent_ingestion_service import ingest_metrics_request
from onestep_control_plane_api.api.routers.prometheus import (
    _build_prometheus_metrics,
    build_observability_metrics,
    build_prometheus_metrics,
    reset_prometheus_metrics_cache,
)
from onestep_control_plane_api.api.schemas import MetricsIngestRequest
from onestep_control_plane_api.core.settings import settings
from onestep_control_plane_api.db.session import session_scope
from sqlalchemy import event


def ingest_metrics(db_session, payload: dict[str, object]):
    """Drive the async metrics ingest from a synchronous test.

    The work unit opens its own session on the test's async engine, which is the
    same database the synchronous ``db_session`` fixture reads.
    """

    async def _work_unit() -> None:
        async with session_scope() as session:
            await ingest_metrics_request(
                session, MetricsIngestRequest.model_validate(payload)
            )

    return asyncio.run(_work_unit())

INSTANCE_ID = "33fb10d0-7580-4552-b8ca-4ef55f98f844"


def _metrics_payload(
    *,
    suffix: str,
    succeeded: int,
    failed: int,
    inflight: int,
) -> dict[str, object]:
    return {
        "service": {
            "name": "billing-sync",
            "environment": "prod",
            "node_name": "vm-prod-3",
            "instance_id": INSTANCE_ID,
            "deployment_version": "1.0.0a0+c435c99",
        },
        "sent_at": "2026-03-08T17:31:00Z",
        "sequence": 3,
        "window": {
            "started_at": f"2026-03-08T17:{suffix}:00Z",
            "ended_at": f"2026-03-08T17:{suffix}:30Z",
        },
        "tasks": [
            {
                "task_name": "sync_users",
                "window_id": f"sync_users:{suffix}",
                "fetched": succeeded + failed,
                "started": succeeded + failed,
                "succeeded": succeeded,
                "retried": 0,
                "failed": failed,
                "dead_lettered": 0,
                "cancelled": 0,
                "timeouts": 0,
                "inflight": inflight,
                "avg_duration_ms": 134.2,
                "p95_duration_ms": 280.0,
                "custom_metrics": [
                    {
                        "name": "rows_success",
                        "kind": "counter",
                        "value": succeeded,
                        "labels": {},
                    },
                    {
                        "name": "rows_failed",
                        "kind": "counter",
                        "value": failed,
                        "labels": {"reason": "bad\"input\nline"},
                    },
                    {
                        "name": "batch_size",
                        "kind": "gauge",
                        "value": succeeded + failed,
                        "labels": {},
                    },
                ],
            }
        ],
    }


def test_prometheus_metrics_requires_bearer_token(client) -> None:
    response = client.get("/metrics")

    assert response.status_code == 401


def test_prometheus_metrics_exports_runtime_and_custom_metrics(
    client,
    db_session,
    auth_headers,
) -> None:
    ingest_metrics(db_session, _metrics_payload(suffix="30", succeeded=118, failed=2, inflight=2))
    ingest_metrics(db_session, _metrics_payload(suffix="31", succeeded=5, failed=1, inflight=0))

    response = client.get("/metrics", headers=auth_headers)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    body = response.text
    assert (
        'onestep_task_succeeded_total{environment="prod",'
        f'instance_id="{INSTANCE_ID}",service="billing-sync",task="sync_users"}} 123'
    ) in body
    assert (
        'onestep_task_failed_total{environment="prod",'
        f'instance_id="{INSTANCE_ID}",service="billing-sync",task="sync_users"}} 3'
    ) in body
    assert (
        'onestep_task_inflight{environment="prod",'
        f'instance_id="{INSTANCE_ID}",service="billing-sync",task="sync_users"}} 0'
    ) in body
    assert (
        'onestep_task_custom_counter_total{environment="prod",'
        f'instance_id="{INSTANCE_ID}",metric="rows_success",service="billing-sync",'
        'task="sync_users"} 123'
    ) in body
    assert (
        'onestep_task_custom_counter_total{environment="prod",'
        f'instance_id="{INSTANCE_ID}",metric="rows_failed",reason="bad\\"input\\nline",'
        'service="billing-sync",task="sync_users"} 3'
    ) in body
    assert (
        'onestep_task_custom_gauge{environment="prod",'
        f'instance_id="{INSTANCE_ID}",metric="batch_size",service="billing-sync",'
        'task="sync_users"} 6'
    ) in body


def test_prometheus_metrics_keeps_custom_metric_kinds_separate(
    client,
    db_session,
    auth_headers,
) -> None:
    counter_payload = _metrics_payload(suffix="30", succeeded=3, failed=0, inflight=0)
    counter_payload["tasks"][0]["custom_metrics"] = [
        {
            "name": "shared_metric",
            "kind": "counter",
            "value": 3,
            "labels": {"tenant": "acme"},
        }
    ]
    gauge_payload = _metrics_payload(suffix="31", succeeded=0, failed=0, inflight=0)
    gauge_payload["tasks"][0]["custom_metrics"] = [
        {
            "name": "shared_metric",
            "kind": "gauge",
            "value": 7,
            "labels": {"tenant": "acme"},
        }
    ]
    ingest_metrics(db_session, counter_payload)
    ingest_metrics(db_session, gauge_payload)

    response = client.get("/metrics", headers=auth_headers)

    assert response.status_code == 200
    body = response.text
    assert (
        'onestep_task_custom_counter_total{environment="prod",'
        f'instance_id="{INSTANCE_ID}",metric="shared_metric",service="billing-sync",'
        'task="sync_users",tenant="acme"} 3'
    ) in body
    assert (
        'onestep_task_custom_gauge{environment="prod",'
        f'instance_id="{INSTANCE_ID}",metric="shared_metric",service="billing-sync",'
        'task="sync_users",tenant="acme"} 7'
    ) in body


def test_prometheus_metrics_reuses_cached_response(db_session, async_db, monkeypatch) -> None:
    """The database-derived body is cached; only the observability section is fresh.

    ``_compose_prometheus_metrics`` documents this split: the aggregation queries are
    cached for ``prometheus_cache_ttl_s``, while the process-local samples are rendered
    on every scrape so they cannot go stale behind that cache. The observability
    section is stubbed out here, which is what makes ``first == second`` a statement
    about the CACHED body rather than about per-scrape timestamps
    (``db_pool_occupancy_timestamp_seconds`` moves on every scrape by design). The
    statement counter is the real cache assertion.
    """

    monkeypatch.setattr(settings, "prometheus_cache_ttl_s", 60.0)
    monkeypatch.setattr(
        "onestep_control_plane_api.api.routers.prometheus.build_observability_metrics",
        lambda: "",
    )
    reset_prometheus_metrics_cache()
    ingest_metrics(db_session, _metrics_payload(suffix="30", succeeded=118, failed=2, inflight=2))

    statement_count = 0

    def count_statements(*_args: object) -> None:
        nonlocal statement_count
        statement_count += 1

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", count_statements)
    try:
        first = build_prometheus_metrics(db_session)
        count_after_first_scrape = statement_count
        second = build_prometheus_metrics(db_session)
    finally:
        event.remove(engine, "before_cursor_execute", count_statements)
        reset_prometheus_metrics_cache()

    assert "onestep_task_succeeded_total" in first
    assert first == second
    assert count_after_first_scrape > 0
    assert statement_count == count_after_first_scrape


def test_prometheus_scrape_samples_pool_occupancy(db_session, monkeypatch) -> None:
    """A scrape refreshes the occupancy gauges, which nothing else ever did.

    ``refresh_pool_occupancy`` is documented as sampled on demand so the gauge agrees
    with the pool at read time, but before this wiring no production caller existed:
    the series were never emitted, so the saturation signal
    ``db_pool_checked_out / db_pool_size`` from the latency runbook had no data and no
    alert could be built on it.
    """

    monkeypatch.setattr(settings, "prometheus_cache_ttl_s", 0.0)
    reset_prometheus_metrics_cache()

    body = build_prometheus_metrics(db_session)

    assert "onestep_control_plane_db_pool_checked_out{" in body
    assert "onestep_control_plane_db_pool_size{" in body
    assert "onestep_control_plane_db_pool_occupancy_timestamp_seconds{" in body


# --------------------------------------------------------------------------------------
# Alert-rule / emitter agreement
# --------------------------------------------------------------------------------------


def test_every_alert_rule_metric_is_emitted_by_the_exporter() -> None:
    """No rule may reference a series the control plane does not emit.

    This is the regression guard for the defect that motivated the counter wiring:
    ``monitoring/prometheus/rules/control-plane.yml`` referenced
    ``onestep_control_plane_ui_ws_disconnects_total``,
    ``onestep_control_plane_agent_commands_total`` and
    ``onestep_control_plane_notification_deliveries_total``, but no code emitted
    them, so three shipped alerts could never fire and nothing failed when that was
    true. A rule is a promise that a series exists; this test checks the promise
    against the exporter's own output.

    Scope note: only series under the ``onestep_control_plane_`` prefix are checked.
    The availability rules legitimately read series from other systems
    (``up``, ``probe_success``, ``pg_up``), which this exporter must NOT emit.
    """

    import re
    from pathlib import Path

    import yaml

    rules_path = (
        Path(__file__).resolve().parents[2]
        / "monitoring"
        / "prometheus"
        / "rules"
        / "control-plane.yml"
    )
    assert rules_path.exists(), f"alert rules not found at {rules_path}"
    document = yaml.safe_load(rules_path.read_text(encoding="utf-8"))

    # Every onestep_control_plane_* identifier appearing in an expression.
    referenced: set[str] = set()
    for group in document["groups"]:
        for rule in group["rules"]:
            for name in re.findall(r"\bonestep_control_plane_[a-z0-9_]+", rule["expr"]):
                # Histogram rules read the _bucket suffix; the family is the emitter's
                # name, so normalize it back.
                referenced.add(name.removesuffix("_bucket"))

    assert referenced, "no onestep_control_plane_* series referenced by any rule"

    # Drive every documented emitter so the check asks "can the exporter emit this
    # series at all?" rather than "did it happen to be non-empty in a fresh
    # process?". Several families are legitimately absent until their first
    # observation (the lag gauges need a sample, the scan families need a scan run,
    # occupancy needs a scrape-time refresh), so an empty fresh process would prove
    # nothing about whether the code path exists.
    from onestep_control_plane_api.ops import observability as obs

    obs.get_event_loop_lag_sampler().record_sample(0.01)
    with obs.scan_duration_timer("notification_missed_start"):
        pass
    obs.record_pool_wait(0.02, pool_class="QueuePool", pool_name="alert_rule_pool")
    obs.record_agent_command_outcome("failed")
    obs.record_notification_delivery_outcome("failed")
    obs.record_ui_stream_disconnect("error")

    # Occupancy is sampled on the scrape path, so exercise that real path.
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    try:
        obs.refresh_pool_occupancy(engine, name="alert_rule_pool")
        body = build_observability_metrics()
    finally:
        engine.dispose()

    emitted = set(re.findall(r"^# TYPE ([a-z0-9_]+) ", body, re.MULTILINE))

    missing = sorted(referenced - emitted)
    assert missing == [], (
        "alert rules reference series the exporter never emits, so those alerts can "
        f"never fire: {missing}"
    )


def test_every_alert_rule_runbook_anchor_resolves() -> None:
    """Every rule's ``runbook`` link must point at a heading that exists.

    A broken anchor sends an operator to the top of the runbook mid-incident. This
    is cheap to check and easy to break: the anchor is a GitHub heading slug, so
    renaming a heading silently invalidates every link to it.
    """

    import re
    from pathlib import Path

    import yaml

    docs_dir = Path(__file__).resolve().parents[2] / "docs"
    rules_path = docs_dir.parent / "monitoring" / "prometheus" / "rules" / "control-plane.yml"
    document = yaml.safe_load(rules_path.read_text(encoding="utf-8"))

    def slug(heading: str) -> str:
        lowered = heading.strip().lower()
        return re.sub(r"\s+", "-", re.sub(r"[^\w\s-]", "", lowered))

    checked = 0
    for group in document["groups"]:
        for rule in group["rules"]:
            target = rule["annotations"].get("runbook")
            assert target, f"{rule['alert']} has no runbook annotation"
            relative, _, anchor = target.partition("#")
            path = docs_dir / relative.removeprefix("docs/")
            assert path.exists(), f"{rule['alert']} points at a missing file: {relative}"

            headings = {
                slug(match.group(1))
                for match in re.finditer(r"^#{1,6}\s+(.*)$", path.read_text(encoding="utf-8"), re.M)
            }
            assert anchor in headings, (
                f"{rule['alert']} links to #{anchor}, which is not a heading in {relative}"
            )
            checked += 1

    assert checked == sum(len(group["rules"]) for group in document["groups"])


# --------------------------------------------------------------------------------------
# Monitoring configuration: the rules must actually be loaded and scorable
# --------------------------------------------------------------------------------------


def test_prometheus_config_loads_the_rule_file() -> None:
    """The alert rules must be reachable through a ``rule_files`` entry.

    Regression guard for the defect that motivated this wiring: twelve rules
    shipped in ``monitoring/prometheus/rules/control-plane.yml`` while no Prometheus
    config anywhere in the repo contained a ``rule_files`` entry, so nothing ever
    loaded them. They were correct and inert, and no test noticed.
    """

    from pathlib import Path

    import yaml

    config_path = (
        Path(__file__).resolve().parents[2] / "monitoring" / "prometheus" / "prometheus.yml"
    )
    assert config_path.exists(), f"prometheus.yml not found at {config_path}"

    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    rule_files = config.get("rule_files")
    assert rule_files, "prometheus.yml declares no rule_files, so no rule is ever loaded"

    # The rule file must be matched by at least one glob, or it loads nothing.
    from fnmatch import fnmatch

    actual = "control-plane.yml"
    assert any(fnmatch(actual, pattern.rsplit("/", 1)[-1]) for pattern in rule_files), (
        f"{actual} is not matched by any rule_files glob: {rule_files}"
    )


def test_every_scrape_job_a_rule_selects_on_is_defined() -> None:
    """Every ``job="..."`` a rule selects on must exist in the scrape config.

    ``promtool check config`` validates each file alone, so it cannot see this: a
    rule selecting on a misspelled job name is valid PromQL, loads without
    complaint, and simply never fires -- indistinguishable from a healthy system.
    This mirrors the check in ``scripts/check-monitoring.sh`` so the backend suite
    fails even when the Docker-based script is not run.
    """

    import re
    from pathlib import Path

    import yaml

    monitoring = Path(__file__).resolve().parents[2] / "monitoring" / "prometheus"
    config = yaml.safe_load((monitoring / "prometheus.yml").read_text(encoding="utf-8"))

    defined_jobs = {
        job["job_name"] for job in config.get("scrape_configs", []) if "job_name" in job
    }
    assert defined_jobs, "prometheus.yml defines no scrape jobs"

    referenced_jobs: set[str] = set()
    for rule_file in (monitoring / "rules").glob("*.yml"):
        document = yaml.safe_load(rule_file.read_text(encoding="utf-8"))
        for group in document.get("groups", []):
            for rule in group.get("rules", []):
                referenced_jobs.update(re.findall(r'job\s*=\s*"([^"]+)"', rule["expr"]))

    assert referenced_jobs, "no rule selects on a job label; this test would prove nothing"

    missing = sorted(referenced_jobs - defined_jobs)
    assert missing == [], (
        "these jobs are selected by an alert rule but are not scraped, so those "
        f"alerts can never fire: {missing} (defined: {sorted(defined_jobs)})"
    )


def test_alertmanager_inhibits_alerts_derived_from_an_api_outage() -> None:
    """A root-cause alert must suppress the symptoms it explains.

    Without inhibition, ``OneStepControlPlaneApiDown`` fires alongside the eight
    alerts that are consequences of it, and an operator paged nine times learns
    less than one paged once with the cause named.
    """

    from pathlib import Path

    import yaml

    path = (
        Path(__file__).resolve().parents[2]
        / "monitoring"
        / "alertmanager"
        / "alertmanager.yml"
    )
    assert path.exists(), f"alertmanager.yml not found at {path}"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))

    inhibits = config.get("inhibit_rules") or []
    assert inhibits, "alertmanager.yml defines no inhibit_rules"

    sources = {
        matcher.split("=", 1)[1].strip().strip('"')
        for rule in inhibits
        for matcher in rule.get("source_matchers", [])
        if matcher.startswith("alertname")
    }
    assert "OneStepControlPlaneApiDown" in sources, (
        "nothing inhibits the alerts derived from the API being down"
    )


def test_alertmanager_config_has_a_receiver_for_every_route() -> None:
    """Every route must point at a receiver that exists, or alerts are dropped."""

    from pathlib import Path

    import yaml

    path = (
        Path(__file__).resolve().parents[2]
        / "monitoring"
        / "alertmanager"
        / "alertmanager.yml"
    )
    config = yaml.safe_load(path.read_text(encoding="utf-8"))

    receivers = {receiver["name"] for receiver in config.get("receivers", [])}
    assert receivers, "alertmanager.yml defines no receivers"

    route = config.get("route", {})
    referenced = {route["receiver"]} if route.get("receiver") else set()
    referenced.update(
        child["receiver"] for child in route.get("routes", []) if child.get("receiver")
    )

    missing = sorted(referenced - receivers)
    assert missing == [], f"routes reference undefined receivers: {missing}"


# --------------------------------------------------------------------------------------
# Absence guards: "no data" must not read as "healthy"
# --------------------------------------------------------------------------------------


def _load_rule_file() -> dict:
    from pathlib import Path

    import yaml

    path = (
        Path(__file__).resolve().parents[2]
        / "monitoring"
        / "prometheus"
        / "rules"
        / "control-plane.yml"
    )
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _all_rules() -> list[dict]:
    return [rule for group in _load_rule_file()["groups"] for rule in group["rules"]]


def test_absence_guards_use_and_on() -> None:
    """An ``absent()`` guard must use ``and on()``, not a bare ``and``.

    This is the trap the guards exist to avoid, and it is invisible in review: a bare
    ``and`` matches on ALL labels, and ``absent()`` returns a series with an EMPTY
    label set, so ``up{job="x"} and absent(...)`` matches nothing and the guard never
    fires. A guard that can never fire is worse than no guard -- it looks like
    coverage.

    Verified against Prometheus 2.53: the bare form returned 0 series while the
    ``and on()`` form returned 1 for the same missing family.
    """

    import re

    guards = [rule for rule in _all_rules() if "absent(" in rule["expr"]]
    assert guards, "no absence guards found; this test would prove nothing"

    for rule in guards:
        expr = rule["expr"]
        assert re.search(r"\band\s+on\(\)", expr), (
            f"{rule['alert']} uses absent() without `and on()`, so it can never fire: {expr}"
        )


def test_absence_guards_require_the_target_to_be_up() -> None:
    """A guard must be gated on ``up == 1``.

    Without that gate the guard also fires while the target is DOWN, which is a
    different incident already covered by OneStepControlPlaneApiDown. The gate is what
    makes the guard answer the question it is named for: "the target is up but its
    data is gone".
    """

    guards = [rule for rule in _all_rules() if "absent(" in rule["expr"]]
    assert guards

    for rule in guards:
        assert 'up{job="onestep-control-plane"} == 1' in rule["expr"], (
            f"{rule['alert']} is not gated on the target being up: {rule['expr']}"
        )


def test_absence_guards_do_not_overlap_with_api_down() -> None:
    """Guards must be mutually exclusive with ``ApiDown`` by construction.

    A guard requires ``up == 1``; ApiDown fires on ``up == 0``. They can therefore
    never both be true, which is why the guards are deliberately absent from
    ApiDown's inhibit list. If a guard ever loses its ``up == 1`` gate, this test
    fails -- and the guard would then double-report every outage.
    """

    api_down = next(r for r in _all_rules() if r["alert"] == "OneStepControlPlaneApiDown")
    assert "== 0" in api_down["expr"]

    for rule in _all_rules():
        if "absent(" not in rule["expr"]:
            continue
        assert "== 0" not in rule["expr"], (
            f"{rule['alert']} combines an absence guard with an `== 0` test, which is "
            f"contradictory: {rule['expr']}"
        )


def test_alerts_whose_series_can_be_absent_have_a_guard() -> None:
    """Families that only appear after their first observation need a guard.

    These three families are emitted lazily -- the scan families only after a scan
    runs, the lag percentiles only after a sample is recorded, occupancy only after a
    scrape refreshes it. Until then their alerts are silent, which is the failure
    mode this test pins: silence must be distinguishable from health.
    """

    guarded = {
        match
        for rule in _all_rules()
        if "absent(" in rule["expr"]
        for match in __import__("re").findall(r"absent\(([a-z0-9_]+)\)", rule["expr"])
    }

    for family in (
        "onestep_control_plane_scan_runs_total",
        "onestep_control_plane_event_loop_lag_p95_seconds",
        "onestep_control_plane_db_pool_checked_out",
        "onestep_control_plane_event_loop_lag_sampler_running",
    ):
        assert family in guarded, f"{family} can be absent but has no absence guard"


def test_alerting_failure_alerts_are_critical() -> None:
    """Alerts that mean "you have lost the ability to be told about problems" page.

    A failing scan or failed notification delivery does not merely degrade a metric:
    it stops the notification plane from reporting, so the operator goes blind without
    being told. That is strictly more dangerous than an outage that announces itself,
    which is why these two are critical rather than warning. Both carry `for: 15m`
    over a 15m window, so the condition must hold for roughly 30 minutes -- this is
    not a page on a single blip.
    """

    by_name = {rule["alert"]: rule for rule in _all_rules()}

    for alert in (
        "OneStepControlPlaneScanFailing",
        "OneStepControlPlaneNotificationDeliveryFailures",
        "OneStepControlPlaneMetricsMissing",
        "OneStepControlPlaneScanNeverRan",
    ):
        assert by_name[alert]["labels"]["severity"] == "critical", (
            f"{alert} is a blindness failure and must page"
        )


def test_guards_are_subsumed_by_the_broadest_guard() -> None:
    """When every family is missing, report the root cause once, not four times."""

    from pathlib import Path

    import yaml

    path = (
        Path(__file__).resolve().parents[2]
        / "monitoring"
        / "alertmanager"
        / "alertmanager.yml"
    )
    config = yaml.safe_load(path.read_text(encoding="utf-8"))

    subsumption = [
        rule
        for rule in config["inhibit_rules"]
        if any("MetricsMissing" in m for m in rule.get("source_matchers", []))
    ]
    assert subsumption, "MetricsMissing does not inhibit the narrower absence guards"
    targets = " ".join(subsumption[0]["target_matchers"])
    for narrower in ("ScanNeverRan", "LagWindowEmpty", "DbPoolOccupancyMissing"):
        assert narrower in targets, f"MetricsMissing should subsume {narrower}"


# --------------------------------------------------------------------------------------
# Task metrics are rolling sums, not counters
# --------------------------------------------------------------------------------------


def test_task_metric_windows_are_not_monotonic_counters(db_session) -> None:
    """Prove the task totals DECREASE when retention prunes, then pin the type.

    These families look like counters and even keep a `_total` suffix, but their value
    is `SUM(TaskMetricWindow.*)` over every window still inside retention, and the
    retention worker deletes windows older than
    `retention_task_metric_windows_days` (90 by default). The sum therefore drops on
    every retention pass.

    Declaring them `counter` is not a cosmetic mistake. Prometheus treats a counter
    decrease as a reset and folds the whole drop into the next `increase()`, so a rule
    using `increase()` over these families would spike by roughly the entire retained
    total after every retention run. That is the real reason there is no task
    throughput or failure-rate alert.
    """

    from datetime import UTC, datetime, timedelta

    from onestep_control_plane_api.db.models import Instance, Service, TaskMetricWindow

    now = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
    service = Service(name="metrics-probe", environment="prod", latest_deployment_version="1")
    db_session.add(service)
    db_session.flush()
    instance = Instance(
        service_id=service.id,
        instance_id=__import__("uuid").uuid4(),
        node_name="vm",
        deployment_version="1",
        status="ok",
    )
    db_session.add(instance)
    db_session.flush()

    def manufactured_total() -> float:
        return float(
            db_session.execute(
                sa.select(sa.func.sum(TaskMetricWindow.succeeded)).where(
                    TaskMetricWindow.service_id == service.id
                )
            ).scalar()
            or 0
        )

    for index, (ended_at, succeeded) in enumerate(
        ((now, 100), (now - timedelta(days=100), 50))
    ):
        db_session.add(
            TaskMetricWindow(
                service_id=service.id,
                instance_id=instance.instance_id,
                task_name="probe",
                window_id=f"probe-{index}",
                window_started_at=ended_at - timedelta(minutes=1),
                window_ended_at=ended_at,
                fetched=succeeded,
                started=succeeded,
                succeeded=succeeded,
                retried=0,
                failed=0,
                dead_lettered=0,
                cancelled=0,
                timeouts=0,
                inflight=0,
                avg_duration_ms=1.0,
                received_at=ended_at,
                created_at=ended_at,
            )
        )
    db_session.commit()

    before = manufactured_total()
    assert before == 150

    # Exactly what the retention worker does for windows older than the cutoff.
    db_session.execute(
        sa.delete(TaskMetricWindow).where(
            TaskMetricWindow.window_ended_at < now - timedelta(days=90)
        )
    )
    db_session.commit()

    after = manufactured_total()
    assert after == 100, "retention should have removed the 50 older successes"
    assert after < before, (
        "the task totals must be allowed to decrease -- if this ever becomes "
        "monotonic, the metric can be declared a counter again"
    )


def test_task_totals_are_exported_as_gauges() -> None:
    """The declared type must match the non-monotonic value.

    Companion to the test above: that one proves the VALUE can decrease, this one
    proves the EXPOSITION says so. Both are needed -- a future edit could flip the
    type back to `counter` without changing the value, and only this asserts it.
    """

    rendered = _render_task_metric_types()
    for name in (
        "onestep_task_succeeded_total",
        "onestep_task_failed_total",
        "onestep_task_custom_counter_total",
    ):
        assert rendered[name] == "gauge", (
            f"{name} must be a gauge: its value is a rolling sum over retained windows "
            f"and decreases when retention prunes, so `counter` would make "
            f"increase()/rate() spike after every retention run"
        )


def _render_task_metric_types() -> dict[str, str]:
    """Read the declared TYPE of the task families out of a live exposition.

    ``_build_prometheus_metrics`` needs a database session for the values, but the
    TYPE lines are emitted from static code, so an in-memory session is enough.
    """

    import re

    from onestep_control_plane_api.db.base import Base
    from onestep_control_plane_api.db.models import Service
    from sqlalchemy.orm import Session

    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    try:
        session.add(Service(name="type-probe", environment="prod", latest_deployment_version="1"))
        session.commit()
        body = _build_prometheus_metrics(session)
    finally:
        session.close()
        engine.dispose()
    return dict(re.findall(r"^# TYPE (onestep_task_[a-z_]+) (\w+)$", body, re.MULTILINE))


def test_no_alert_rule_uses_increase_or_rate_on_task_totals() -> None:
    """No rule may treat the task rolling sums as monotonic counters.

    This is the enforcement half of the pair above: the types are honest *and* no
    rule relies on the dishonest reading. A rule doing
    `increase(onestep_task_failed_total[1h]) > N` would appear to work in testing and
    then fire with a huge bogus value after the first retention pass.
    """

    import re
    from pathlib import Path

    import yaml

    path = (
        Path(__file__).resolve().parents[2]
        / "monitoring"
        / "prometheus"
        / "rules"
        / "control-plane.yml"
    )
    document = yaml.safe_load(path.read_text(encoding="utf-8"))

    offenders = [
        (group["name"], rule["alert"], rule["expr"])
        for group in document["groups"]
        for rule in group["rules"]
        if "onestep_task_" in rule["expr"]
        and re.search(r"\b(increase|rate)\s*\(", rule["expr"])
    ]
    assert offenders == [], (
        "these rules use increase()/rate() on the task rolling sums, which decrease "
        f"when retention prunes and would spike after every retention run: {offenders}"
    )
