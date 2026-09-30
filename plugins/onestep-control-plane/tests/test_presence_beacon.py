from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import pytest

from onestep import OneStepApp
from onestep_control_plane import (
    PRESENCE_PATH,
    ControlPlaneReporter,
    ControlPlaneReporterConfig,
    PresenceBeacon,
)

from control_plane_testkit import FIXED_INSTANCE_ID, SenderRecorder, make_config

SERVICE_DESCRIPTOR: dict[str, Any] = {
    "name": "billing-sync",
    "environment": "prod",
    "node_name": "vm-prod-3",
    "instance_id": FIXED_INSTANCE_ID,
    "deployment_version": "1.0.0+c435c99",
}


class FakeClock:
    """Monotonic clock the test drives by hand.

    The beacon decides whether to send by comparing this clock's reading with
    the last tick, so a test can produce a "stalled loop" instantly instead of
    sleeping for the real grace window.
    """

    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, delta: float) -> None:
        self.value += delta


@dataclass
class RecordingPost:
    status: int = 200
    raise_exc: Exception | None = None
    calls: list[tuple[str, str, dict[str, Any], float]] = field(default_factory=list)
    called: threading.Event = field(default_factory=threading.Event)

    def __call__(
        self,
        url: str,
        token: str,
        payload: dict[str, Any],
        timeout_s: float,
    ) -> int:
        self.calls.append((url, token, payload, timeout_s))
        self.called.set()
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.status


def wait_until(predicate, *, timeout_s: float = 3.0, interval_s: float = 0.005) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval_s)
    return predicate()


def make_beacon(
    *,
    post: RecordingPost | None = None,
    clock: FakeClock | None = None,
    interval_s: float = 0.02,
    # The tightest ratio the beacon accepts; see the config invariant.
    grace_s: float = 0.04,
    **overrides: Any,
) -> PresenceBeacon:
    kwargs: dict[str, Any] = {
        "base_url": "https://control-plane.example.com",
        "token": "secret-token",
        "service_descriptor": dict(SERVICE_DESCRIPTOR),
        "interval_s": interval_s,
        "grace_s": grace_s,
        "http_post": post or RecordingPost(),
        "clock": clock or FakeClock(),
    }
    kwargs.update(overrides)
    return PresenceBeacon(**kwargs)


def test_beacon_posts_to_the_documented_presence_path() -> None:
    beacon = make_beacon()

    assert PRESENCE_PATH == "/api/v1/agents/presence"
    assert beacon.url == f"https://control-plane.example.com{PRESENCE_PATH}"
    # The WS URL helper is reused, so a websocket base url maps onto https.
    assert make_beacon(base_url="wss://control-plane.example.com/api/v1/agents/ws").url == (
        f"https://control-plane.example.com{PRESENCE_PATH}"
    )


def test_beacon_sends_nothing_while_the_loop_is_alive() -> None:
    """A healthy process must be byte-for-byte invisible: zero requests."""
    post = RecordingPost()
    beacon = make_beacon(post=post, interval_s=0.05, grace_s=0.1)
    beacon.start()
    try:
        deadline = time.monotonic() + 0.3
        while time.monotonic() < deadline:
            beacon.touch()
            time.sleep(0.01)
    finally:
        beacon.stop()

    assert post.calls == []
    assert beacon.sent_count == 0


def test_beacon_reports_liveness_once_the_loop_is_stalled() -> None:
    post = RecordingPost()
    clock = FakeClock()
    beacon = make_beacon(post=post, clock=clock, interval_s=0.02, grace_s=0.04)
    beacon.start()
    try:
        clock.advance(1.0)
        assert wait_until(lambda: bool(post.calls))
    finally:
        beacon.stop()

    url, token, payload, _timeout = post.calls[0]
    assert url == beacon.url
    assert token == "secret-token"
    assert set(payload) == {"service", "sent_at"}
    assert payload["service"]["instance_id"] == FIXED_INSTANCE_ID
    assert payload["service"]["name"] == "billing-sync"
    assert beacon.sent_count >= 1


def test_beacon_touch_silences_an_already_stalled_loop() -> None:
    post = RecordingPost()
    clock = FakeClock()
    beacon = make_beacon(post=post, clock=clock, interval_s=0.05, grace_s=0.1)
    clock.advance(10.0)
    beacon.start()
    try:
        # The loop reports in again before the beacon's next wake-up.
        beacon.touch()
        time.sleep(0.2)
    finally:
        beacon.stop()

    assert post.calls == []


def test_beacon_stays_silent_for_a_healthy_loop_at_the_tightest_legal_ratio() -> None:
    """Regression guard for the zero-traffic promise under real scheduling.

    ``grace == 2 * interval`` is the tightest ratio the config accepts, and it
    is the shape a real deployment has: the heartbeat loop touches once per
    interval, and the beacon's sleep is half a grace -- the same duration. The
    sleep is therefore always interrupted by a touch before it completes, so a
    healthy process never reports. Sleeping the full grace and then re-checking
    the clock would instead send whenever the scheduler ran a few milliseconds
    late, which is exactly what this pins.
    """

    post = RecordingPost()
    beacon = make_beacon(post=post, interval_s=0.05, grace_s=0.1)
    beacon.start()
    try:
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline:
            beacon.touch()
            # The heartbeat loop's own period: exactly the beacon's sleep, and
            # jittered, so the two never line up cleanly.
            time.sleep(0.045 + (time.monotonic() % 0.01))
    finally:
        beacon.stop()

    assert post.calls == []
    assert beacon.sent_count == 0


@pytest.mark.parametrize("status", [401, 403, 404, 405, 409, 422, 501])
def test_beacon_disables_itself_once_on_a_fatal_status(status: int) -> None:
    """Deterministic rejections stop the beacon after exactly one attempt.

    404 is the backward-compatibility case: an older control plane has no
    presence route, so the beacon gives up and the WebSocket heartbeat carries
    liveness as it always did.
    """

    post = RecordingPost(status=status)
    clock = FakeClock()
    beacon = make_beacon(post=post, clock=clock, interval_s=0.02, grace_s=0.04)
    beacon.start()
    try:
        clock.advance(1.0)
        assert wait_until(lambda: beacon.disabled_reason is not None)
        # Once disabled the thread exits, so the retry loop stops for good.
        assert wait_until(lambda: not beacon.running)
    finally:
        beacon.stop()

    assert beacon.disabled_reason == f"HTTP {status}"
    assert len(post.calls) == 1


@pytest.mark.parametrize("status", [429, 500, 502, 503])
def test_beacon_retries_a_status_that_means_come_back_later(status: int) -> None:
    """Rate limits and server errors are transient, so the beacon keeps trying.

    Disabling on these would silently drop the loop-stall protection for the
    rest of the process's life over a momentary overload.
    """

    post = RecordingPost(status=status)
    clock = FakeClock()
    beacon = make_beacon(post=post, clock=clock, interval_s=0.02, grace_s=0.04)
    beacon.start()
    try:
        clock.advance(1.0)
        assert wait_until(lambda: len(post.calls) >= 2)
    finally:
        beacon.stop()

    assert beacon.disabled_reason is None
    assert beacon.sent_count == 0


def test_beacon_retries_transport_failures_without_raising() -> None:
    post = RecordingPost(raise_exc=OSError("connection refused"))
    clock = FakeClock()
    beacon = make_beacon(post=post, clock=clock, interval_s=0.02, grace_s=0.04)
    beacon.start()
    try:
        clock.advance(1.0)
        assert wait_until(lambda: len(post.calls) >= 2)
    finally:
        beacon.stop()

    assert beacon.disabled_reason is None
    assert beacon.sent_count == 0


def test_beacon_rejects_a_grace_below_twice_the_interval() -> None:
    """The zero-traffic promise is enforced at the object, not only in config.

    A beacon whose sleep can outlast the heartbeat interval would report
    liveness from a perfectly healthy loop. Refusing to build it is better than
    shipping a beacon that lies, and it holds for every construction path
    (the reporter's config validates the same floor).
    """

    with pytest.raises(ValueError, match="grace_s must be >= 2 \\* interval_s"):
        make_beacon(interval_s=10.0, grace_s=19.0)


def test_beacon_stop_joins_the_thread() -> None:
    beacon = make_beacon()
    beacon.start()
    assert beacon.running

    beacon.stop()

    assert not beacon.running
    assert beacon.disabled_reason is None
    # Stopping twice is a no-op rather than an error.
    beacon.stop()


def test_beacon_backoff_grows_but_stays_bounded() -> None:
    clock = FakeClock()
    beacon = make_beacon(clock=clock, interval_s=10.0, grace_s=20.0)
    clock.advance(100.0)

    delays = []
    for failures in range(6):
        beacon._consecutive_failures = failures
        delays.append(beacon._next_delay_s())

    assert delays == sorted(delays)
    assert delays[0] == 10.0
    assert delays[-1] > delays[0]

    beacon._consecutive_failures = 40
    assert beacon._next_delay_s() == 300.0


def test_beacon_wakes_short_of_the_grace_while_the_loop_is_healthy() -> None:
    """The sleep must never span the whole grace window.

    If it did, a healthy process would report liveness whenever the scheduler
    ran a few milliseconds late -- and "a healthy process sends nothing" is the
    property this whole design rests on. Half a grace means the sleep is always
    interrupted by a touch from a loop keeping to an interval of at most half
    the grace.
    """

    clock = FakeClock()

    # Interval longer than the grace: the grace, not the interval, is the bound.
    beacon = make_beacon(clock=clock, interval_s=300.0, grace_s=600.0)
    assert beacon._next_delay_s() == 300.0

    clock.advance(299.0)
    assert beacon._next_delay_s() == 300.0

    # Stalled: poll on the configured interval instead.
    clock.advance(1000.0)
    assert beacon._next_delay_s() == 300.0


def test_beacon_interval_bounds_the_healthy_wakeup_at_the_tightest_ratio() -> None:
    clock = FakeClock()
    # grace == 2 * interval: both bounds are the same number.
    beacon = make_beacon(clock=clock, interval_s=10.0, grace_s=20.0)

    assert beacon._next_delay_s() == 10.0

    clock.advance(200.0)
    assert beacon._next_delay_s() == 10.0


@dataclass
class BeaconSpy:
    started: int = 0
    stopped: int = 0
    touches: int = 0

    def start(self) -> None:
        self.started += 1

    def stop(self, **_kwargs: Any) -> None:
        self.stopped += 1

    def touch(self) -> None:
        self.touches += 1


def test_reporter_does_not_start_a_beacon_for_an_injected_sender() -> None:
    async def run() -> ControlPlaneReporter:
        app = OneStepApp("billing-sync")
        reporter = ControlPlaneReporter(make_config(), sender=SenderRecorder())
        reporter.attach(app)
        await app.startup()
        await app.shutdown()
        return reporter

    reporter = asyncio.run(run())

    assert reporter._presence is None


def test_reporter_touches_an_explicitly_supplied_beacon() -> None:
    async def run() -> BeaconSpy:
        spy = BeaconSpy()
        app = OneStepApp("billing-sync")
        reporter = ControlPlaneReporter(
            make_config(heartbeat_interval_s=0.01),
            sender=SenderRecorder(),
            presence_beacon=spy,
        )
        reporter.attach(app)
        await app.startup()
        for _ in range(50):
            if spy.touches:
                break
            await asyncio.sleep(0.01)
        await app.shutdown()
        return spy

    spy = asyncio.run(run())

    assert spy.started == 1
    assert spy.stopped == 1
    assert spy.touches >= 1


def test_presence_config_rejects_a_grace_shorter_than_twice_the_interval() -> None:
    """A grace below 2x the interval lets a healthy loop trip the beacon.

    The beacon sleeps half a grace; if that is shorter than the heartbeat
    interval, a perfectly healthy process completes the sleep and reports
    liveness. Rejecting the configuration is better than a beacon that lies.
    """

    with pytest.raises(ValueError, match="presence_grace_s must be >= 2 \\* presence_interval_s"):
        make_config(presence_interval_s=10.0, presence_grace_s=19.0)

    # Exactly 2x is the tightest legal ratio.
    config = make_config(presence_interval_s=10.0, presence_grace_s=20.0)
    assert config.presence_grace_s == 20.0


def test_presence_config_defaults_track_the_heartbeat_interval() -> None:
    config = make_config(heartbeat_interval_s=15.0)

    assert config.presence_enabled is True
    assert config.presence_interval_s == 15.0
    assert config.presence_grace_s == 30.0


def test_presence_grace_follows_an_explicit_interval() -> None:
    """Setting only the interval must not trip the ratio check.

    The invariant is about the beacon's own sleep, so the derived grace tracks
    ``presence_interval_s``. Deriving it from ``heartbeat_interval_s`` instead
    would reject a config that raised the interval above half the heartbeat.
    """

    config = make_config(heartbeat_interval_s=30.0, presence_interval_s=60.0)

    assert config.presence_grace_s == 120.0


def test_presence_can_be_disabled() -> None:
    config = make_config(presence_enabled=False)
    reporter = ControlPlaneReporter(config)

    reporter._start_presence_beacon()

    assert config.presence_enabled is False
    assert reporter._presence is None


def test_presence_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ONESTEP_CONTROL_PLANE_URL", "https://control-plane.example.com")
    monkeypatch.setenv("ONESTEP_CONTROL_PLANE_TOKEN", "secret-token")
    monkeypatch.setenv("ONESTEP_CONTROL_PLANE_PRESENCE_ENABLED", "off")
    monkeypatch.setenv("ONESTEP_CONTROL_PLANE_PRESENCE_INTERVAL_S", "12")
    monkeypatch.setenv("ONESTEP_CONTROL_PLANE_PRESENCE_GRACE_S", "40")

    config = ControlPlaneReporterConfig.from_env(app_name="billing-sync")

    assert config.presence_enabled is False
    assert config.presence_interval_s == 12.0
    assert config.presence_grace_s == 40.0


def test_presence_env_rejects_a_non_boolean(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ONESTEP_CONTROL_PLANE_URL", "https://control-plane.example.com")
    monkeypatch.setenv("ONESTEP_CONTROL_PLANE_TOKEN", "secret-token")
    monkeypatch.setenv("ONESTEP_CONTROL_PLANE_PRESENCE_ENABLED", "sometimes")

    with pytest.raises(ValueError, match="ONESTEP_CONTROL_PLANE_PRESENCE_ENABLED"):
        ControlPlaneReporterConfig.from_env(app_name="billing-sync")
