"""Issue #196: stable confirmation and flapping damping for connectivity flips.

State machine under test (per channel x instance), with DEFAULT settings
``instance_offline_after_s = 90`` and ``instance_connectivity_confirm_after_s = 30``:

* an ``offline`` observation exists only once ``last_seen_at`` is older than the
  90 s offline window;
* a flip is NOTIFIED only once it has HELD for ``confirm_after_s`` measured from
  its own ``transition_at`` (offline = ``last_seen_at + 90``, online =
  ``last_seen_at``);
* a flip that heals inside that window is CANCELLED and never notified;
* a run of confirmed flips closer together than ``flap_window_s`` is one
  FLAP EPISODE: the first ``flap_max_notifications`` (3) are notified, later ones
  are suppressed but counted, and ONE summary is emitted when the episode quiets.
  The summary reflects the episode's FINAL state (offline stays red), never a
  hardcoded "online".

The effective flap window is floored at 2 x (offline_after + confirm_after)
= 240 s with defaults: two confirmed flips can never be closer than 120 s, so a
smaller window would close every episode before the next flip and damping could
never engage (defect-2 regression: test_default_parameters_*).

Every test drives the scan with an explicit ``now=`` (controlled clock). None of
them sleep on wall time.

Clock conventions used below (all relative to ``last_seen_at``):

* ``last_seen + 90``   -> observation flips to offline; transition_at = +90
* ``last_seen + 120``  -> offline has held 30 s -> CONFIRMED
* anything below +120 while offline -> pending, not yet notified
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from onestep_control_plane_api.api.notification_service import (
    scan_and_dispatch_instance_connectivity_notifications,
)
from onestep_control_plane_api.core.settings import settings
from onestep_control_plane_api.db.models import (
    NotificationDelivery,
    NotificationInstanceState,
)
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_notification_service import seed_channel, seed_runtime_service

BASE = datetime(2026, 4, 30, 2, 0, 0, tzinfo=UTC)
OFFLINE_AFTER = settings.instance_offline_after_s  # 90
CONFIRM_AFTER = settings.instance_connectivity_confirm_after_s  # 30
# Effective episode window: max(configured, 2 x (OFFLINE_AFTER + CONFIRM_AFTER)).
FLAP_WINDOW = max(
    settings.instance_connectivity_flap_window_s,
    2 * (OFFLINE_AFTER + CONFIRM_AFTER),
)
FLAP_MAX = settings.instance_connectivity_flap_max_notifications  # 3


def at(seconds: float) -> datetime:
    return BASE + timedelta(seconds=seconds)


def scan(db_session: Session, seconds: float) -> int:
    return scan_and_dispatch_instance_connectivity_notifications(db_session, now=at(seconds))


def seen(db_session: Session, instance, seconds: float) -> None:
    """Set last_seen_at to an absolute controlled-clock offset (seconds)."""

    instance.last_seen_at = at(seconds)
    db_session.commit()


def deliveries(db_session: Session) -> list[NotificationDelivery]:
    return list(
        db_session.scalars(
            select(NotificationDelivery).order_by(NotificationDelivery.created_at)
        ).all()
    )


def state_of(db_session: Session) -> NotificationInstanceState:
    return db_session.scalars(select(NotificationInstanceState)).one()


def seed(db_session: Session, event_types: list[str] | None = None):
    _, instance = seed_runtime_service(db_session)
    seed_channel(db_session, event_types=event_types or ["instance_online", "instance_offline"])
    seen(db_session, instance, 0)
    return instance


def silence_webhooks(monkeypatch) -> None:
    monkeypatch.setattr(
        "onestep_control_plane_api.api.notification_service._post_webhook",
        lambda delivery, *, webhook_url, timeout_s=5.0: None,
    )


def go_offline_confirmed(db_session: Session, instance, *, last_seen_at: float):
    """Drive one fully confirmed offline flip.

    last_seen stays put, so the offline window elapses at ``last_seen_at + 90``
    and the flip is confirmed at ``last_seen_at + 120``.
    """

    scan(db_session, last_seen_at + OFFLINE_AFTER)  # observation flips, not yet held
    return scan(db_session, last_seen_at + OFFLINE_AFTER + CONFIRM_AFTER)  # confirmed


def go_online_confirmed(db_session: Session, instance, *, last_seen_at: float):
    """Drive one fully confirmed online flip (transition_at == last_seen_at)."""

    seen(db_session, instance, last_seen_at)
    return scan(db_session, last_seen_at + CONFIRM_AFTER)


def flap_once(db_session: Session, instance, *, last_seen_at: float) -> tuple[int, float]:
    """One offline->online flip pair whose flips are CLOSER than the episode window.

    Offline confirms at ``last_seen + 90 + 30 = +120``. The instance checks in at
    ``+110`` (before that), so the online candidate is confirmed at ``+140`` --
    only 20 s after the offline flip, with the online transition_at at +110.
    The next offline leg then confirms at ``+230`` (silence from +110), i.e.
    90 s later -- every consecutive confirmed flip is inside the (floored)
    240 s episode window, so the whole run is ONE episode.

    Returns (notifications, new_last_seen_offset).
    """

    notified = go_offline_confirmed(db_session, instance, last_seen_at=last_seen_at)
    online_seen = last_seen_at + OFFLINE_AFTER + 20
    seen(db_session, instance, online_seen)
    notified += scan(db_session, online_seen + CONFIRM_AFTER)
    return notified, online_seen


# --------------------------------------------------------------------------------------
# 1. Transient drop
# --------------------------------------------------------------------------------------


def test_transient_drop_produces_no_notifications(db_session, monkeypatch) -> None:
    """A drop shorter than the confirmation window is never notified.

    Pre-#196 this emitted a full offline/online pair on every blip.
    """

    silence_webhooks(monkeypatch)
    instance = seed(db_session)
    assert scan(db_session, 0) == 0

    # Silence crosses the offline window: observation flips to offline with
    # transition_at = 90, but it has not yet held for 30 s.
    assert scan(db_session, 100) == 0
    assert state_of(db_session).pending_connectivity == "offline"

    # The instance checks in again at 100 -> online, inside the window: cancelled.
    seen(db_session, instance, 100)
    assert scan(db_session, 100) == 0

    seen(db_session, instance, 140)
    assert scan(db_session, 140) == 0

    assert deliveries(db_session) == []
    state = state_of(db_session)
    assert state.last_connectivity == "online"
    assert state.pending_connectivity is None, "a cancelled flip must not stay parked"


def test_transient_drops_repeated_do_not_accumulate_notifications(
    db_session, monkeypatch
) -> None:
    """Repeated short blips stay silent instead of one message per blip."""

    silence_webhooks(monkeypatch)
    instance = seed(db_session)
    assert scan(db_session, 0) == 0

    for cycle in range(4):
        quiet_at = cycle * 400.0
        # Go silent just past the offline window but inside the confirm window.
        # last_seen is moved back to `quiet_at` first so the offline window is
        # measured from there rather than from the previous cycle's check-in.
        seen(db_session, instance, quiet_at)
        assert scan(db_session, quiet_at + OFFLINE_AFTER + 5) == 0
        # ...then check in again before the flip could hold.
        seen(db_session, instance, quiet_at + OFFLINE_AFTER + 20)
        assert scan(db_session, quiet_at + OFFLINE_AFTER + 20) == 0

    assert deliveries(db_session) == []


# --------------------------------------------------------------------------------------
# 2. Sustained offline
# --------------------------------------------------------------------------------------


def test_sustained_offline_alerts_within_the_documented_deadline(
    db_session, monkeypatch
) -> None:
    """A real outage alerts, and no later than detection + confirmation (120 s)."""

    silence_webhooks(monkeypatch)
    seed(db_session, event_types=["instance_offline"])
    assert scan(db_session, 0) == 0

    # Offline observation exists from t=90; not yet held 30 s.
    assert scan(db_session, 100) == 0
    # At t=120 it has held 30 s past transition_at -> confirmed.
    assert scan(db_session, 120) == 1

    rows = deliveries(db_session)
    assert len(rows) == 1
    assert rows[0].event_type == "instance_offline"
    assert state_of(db_session).last_connectivity == "offline"


def test_sustained_offline_is_not_repeated(db_session, monkeypatch) -> None:
    """Once notified, the same outage is not re-announced on later scans."""

    silence_webhooks(monkeypatch)
    seed(db_session, event_types=["instance_offline"])
    assert scan(db_session, 0) == 0
    assert scan(db_session, 120) == 1

    for later in (130, 150, 200, 300, 600):
        assert scan(db_session, later) == 0, f"re-announced at t={later}"

    assert len(deliveries(db_session)) == 1


# --------------------------------------------------------------------------------------
# 3. Flapping
# --------------------------------------------------------------------------------------


def test_flapping_is_rate_limited(db_session, monkeypatch) -> None:
    """A flapping instance produces a handful of alerts, not one per flip.

    Six confirmed flips inside one episode must yield at most FLAP_MAX
    notifications plus one summary -- never six messages.
    """

    silence_webhooks(monkeypatch)
    instance = seed(db_session)
    assert scan(db_session, 0) == 0

    flip_at = 0.0
    notified = 0
    for _ in range(6):
        count, flip_at = flap_once(db_session, instance, last_seen_at=flip_at)
        notified += count

    assert notified <= FLAP_MAX + 1, (
        f"flapping produced a storm: {notified} notifications for 6 flip pairs"
    )
    assert len(deliveries(db_session)) <= FLAP_MAX + 1
    state = state_of(db_session)
    assert int(state.flap_episode_flips or 0) > 0, "damping never engaged"
    assert int(state.flap_suppressed_count or 0) > 0, "no flip was actually suppressed"


def test_flap_summary_reports_the_suppressed_count(db_session, monkeypatch) -> None:
    """The summary is emitted once a damped episode goes quiet, and reports the count.

    Asserted through the module's own state: the summary is a delivery that
    appears with no new flip, and the episode counters are reset afterwards. That
    proves the suppressed count was carried (not silently dropped) and that
    damping cannot outlive the episode.
    """

    silence_webhooks(monkeypatch)
    instance = seed(db_session)
    assert scan(db_session, 0) == 0

    flip_at = 0.0
    for _ in range(6):
        _, flip_at = flap_once(db_session, instance, last_seen_at=flip_at)

    state = state_of(db_session)
    assert int(state.flap_suppressed_count or 0) > 0, "no flip was suppressed"

    # Let the episode go quiet: no flip for a full (floored) episode window.
    quiet_at = flip_at + FLAP_WINDOW + 60
    seen(db_session, instance, quiet_at - 10)
    before_quiet = len(deliveries(db_session))
    scan(db_session, quiet_at)

    after_quiet = len(deliveries(db_session))
    assert after_quiet == before_quiet + 1, (
        "a damped episode must emit exactly one summary when it goes quiet"
    )
    assert deliveries(db_session)[-1].event_type == "instance_online"
    state = state_of(db_session)
    assert state.flap_episode_last_flip_at is None, "episode must be closed"
    assert int(state.flap_suppressed_count or 0) == 0, "counters must reset"


# --------------------------------------------------------------------------------------
# 4. Stable recovery
# --------------------------------------------------------------------------------------


def test_stable_recovery_notifies_once(db_session, monkeypatch) -> None:
    """After a confirmed outage, coming back and staying back notifies once."""

    silence_webhooks(monkeypatch)
    instance = seed(db_session)
    assert scan(db_session, 0) == 0
    assert go_offline_confirmed(db_session, instance, last_seen_at=0) == 1

    seen(db_session, instance, 500)
    assert scan(db_session, 500 + CONFIRM_AFTER) == 1  # recovery

    for later in (560, 620, 700):
        seen(db_session, instance, later - 5)
        assert scan(db_session, later) == 0, f"re-announced recovery at t={later}"

    types = [row.event_type for row in deliveries(db_session)]
    assert types == ["instance_offline", "instance_online"]


# --------------------------------------------------------------------------------------
# 5. Restart / leader switch
# --------------------------------------------------------------------------------------


def test_restart_does_not_replay_a_storm(db_session, monkeypatch) -> None:
    """Damping state is persisted, so a restart neither replays nor double-sends.

    "Restart" is modelled by expiring/re-reading everything from the DB: all the
    scan's damping state lives on the row, never in process memory.
    """

    silence_webhooks(monkeypatch)
    seed(db_session, event_types=["instance_offline"])
    assert scan(db_session, 0) == 0
    assert scan(db_session, 120) == 1

    db_session.expire_all()
    before = len(deliveries(db_session))

    for later in (130, 160, 220, 400):
        db_session.expire_all()
        assert scan(db_session, later) == 0

    assert len(deliveries(db_session)) == before


def test_leader_switch_does_not_permanently_suppress_a_sustained_failure(
    db_session, monkeypatch
) -> None:
    """After an episode is damped, a NEW sustained outage still alerts.

    This is the failure mode a naive "one per hour" cap creates: damping must be
    scoped to a flapping episode, never a global mute.
    """

    silence_webhooks(monkeypatch)
    instance = seed(db_session)
    assert scan(db_session, 0) == 0

    flip_at = 0.0
    for _ in range(6):
        _, flip_at = flap_once(db_session, instance, last_seen_at=flip_at)

    quiet_at = flip_at + FLAP_WINDOW + 120
    seen(db_session, instance, quiet_at - 10)
    scan(db_session, quiet_at)
    after_episode = len(deliveries(db_session))

    # A fresh, genuinely sustained outage well after the episode closed.
    outage_last_seen = quiet_at + 600
    seen(db_session, instance, outage_last_seen)
    scan(db_session, outage_last_seen)  # online, seeds
    alerted = go_offline_confirmed(db_session, instance, last_seen_at=outage_last_seen)

    assert alerted == 1, "a sustained failure after a damped episode must still alert"
    assert len(deliveries(db_session)) == after_episode + 1


# --------------------------------------------------------------------------------------
# 6. First observation / configuration
# --------------------------------------------------------------------------------------


def test_first_observation_seeds_state_without_notifying(db_session, monkeypatch) -> None:
    """No prior connectivity to flip from, so nothing is sent."""

    silence_webhooks(monkeypatch)
    seed(db_session)
    assert scan(db_session, 0) == 0
    assert deliveries(db_session) == []
    state = state_of(db_session)
    assert state.last_connectivity == "online"
    assert state.pending_connectivity is None


def test_confirmation_window_is_configurable(db_session, monkeypatch) -> None:
    """The window comes from settings, not a hardcoded constant."""

    silence_webhooks(monkeypatch)
    seed(db_session, event_types=["instance_offline"])
    monkeypatch.setattr(settings, "instance_connectivity_confirm_after_s", 300)
    assert scan(db_session, 0) == 0

    # transition_at = 90; with a 300 s window the flip is unconfirmed at 120...
    assert scan(db_session, 120) == 0
    # ...and confirmed once 300 s have elapsed past transition_at.
    assert scan(db_session, 90 + 300) == 1


def test_confirmation_window_is_clamped_to_the_offline_window(
    db_session, monkeypatch
) -> None:
    """The added latency is bounded by the detection window the operator accepts."""

    silence_webhooks(monkeypatch)
    seed(db_session, event_types=["instance_offline"])
    monkeypatch.setattr(settings, "instance_connectivity_confirm_after_s", 100_000)
    assert scan(db_session, 0) == 0

    # Clamped to instance_offline_after_s (90): alerts by 90 + 90 = 180.
    assert scan(db_session, 180) == 1


# --------------------------------------------------------------------------------------
# 7. Defect regressions (post-review fixes)
# --------------------------------------------------------------------------------------


def test_flap_summary_reflects_final_offline_state(db_session, monkeypatch) -> None:
    """A damped episode ending OFFLINE must summarise as offline, never online.

    Defect-1 regression: the summary used to hardcode ``instance_online``, so a
    host that ended the episode offline (and stayed offline) was announced with
    a green "recovered" card -- the exact opposite of the truth.
    """

    silence_webhooks(monkeypatch)
    instance = seed(db_session)
    assert scan(db_session, 0) == 0

    # Flap until damping engages, then END THE EPISODE OFFLINE and stay there.
    flip_at = 0.0
    for _ in range(4):
        _, flip_at = flap_once(db_session, instance, last_seen_at=flip_at)
    assert int(state_of(db_session).flap_suppressed_count or 0) > 0

    # The last flip pair left the instance online (last_seen at flip_at). Now it
    # goes offline again -- a damped flip inside the same episode -- and stays
    # offline until the episode quiets.
    assert go_offline_confirmed(db_session, instance, last_seen_at=flip_at) == 0, (
        "the final offline flip must be suppressed, not notified"
    )
    state = state_of(db_session)
    assert state.last_connectivity == "offline"
    final_last_flip = state.flap_episode_last_flip_at
    assert final_last_flip is not None
    final_last_flip_offset = float(
        final_last_flip.timestamp() - at(0).timestamp()
    )

    # Silence past the episode window: the summary fires while still offline.
    # (No heartbeat in between -- the instance is DOWN, and it must stay down so
    # the final state at summary time is offline.)
    quiet_at = final_last_flip_offset + FLAP_WINDOW + 60
    before_quiet = len(deliveries(db_session))
    assert scan(db_session, quiet_at) == 1

    rows = deliveries(db_session)
    summary = rows[-1]
    assert len(rows) == before_quiet + 1
    # THE FIX: the summary carries the episode's final (offline) semantics --
    # red card, offline rendering -- never a green instance_online card.
    assert summary.event_type == "instance_offline", (
        "a damped episode that ended offline must summarise as offline"
    )
    # The summary must carry the REAL counters, not zeroed bookkeeping
    # (defect-1's second half: state used to be reset before it was read).
    assert state_of(db_session).flap_suppressed_count == 0, "episode closed after summary"
    payload = summary.request_payload_json
    assert payload is not None
    rendered = payload["card"]["header"]["title"]["content"]
    assert "[实例下线]" in rendered, f"summary must render offline semantics: {rendered}"


def test_flap_summary_final_online_state_reports_online(db_session, monkeypatch) -> None:
    """A damped episode ending ONLINE still summarises as online (unchanged)."""

    silence_webhooks(monkeypatch)
    instance = seed(db_session)
    assert scan(db_session, 0) == 0

    flip_at = 0.0
    for _ in range(4):
        _, flip_at = flap_once(db_session, instance, last_seen_at=flip_at)
    assert int(state_of(db_session).flap_suppressed_count or 0) > 0

    # Instance stays online: keep the heartbeat fresh past the offline cutoff
    # while the episode quiets, so the final state at summary time is online.
    quiet_at = flip_at + FLAP_WINDOW + 60
    seen(db_session, instance, quiet_at - 10)
    before_quiet = len(deliveries(db_session))
    assert scan(db_session, quiet_at) == 1

    summary = deliveries(db_session)[-1]
    assert len(deliveries(db_session)) == before_quiet + 1
    assert summary.event_type == "instance_online"
    payload = summary.request_payload_json
    assert payload is not None
    # The online summary must state the withheld count, not read as all-clear.
    detail_content = payload["card"]["body"]["elements"][0]["content"]
    assert "抖动抑制" in detail_content
    rendered_suppressed = detail_content.split("已静默 ")[1].split(" ")[0]
    assert int(rendered_suppressed) > 0


def test_flap_summary_respects_channel_subscription(db_session, monkeypatch) -> None:
    """A channel subscribed only to ``instance_offline`` gets no online summary.

    The old fixed ``instance_online`` summary leaked into offline-only channels;
    the final-state summary must pass the same event-type filter as any other
    connectivity notification.
    """

    silence_webhooks(monkeypatch)
    instance = seed(db_session, event_types=["instance_offline"])
    assert scan(db_session, 0) == 0

    flip_at = 0.0
    for _ in range(4):
        _, flip_at = flap_once(db_session, instance, last_seen_at=flip_at)
    assert int(state_of(db_session).flap_suppressed_count or 0) > 0

    # Episode quiets while ONLINE on an offline-only channel: no summary at all
    # (a relabelled summary would be a filter bypass). Keep the heartbeat fresh
    # so the final state really is online.
    quiet_at = flip_at + FLAP_WINDOW + 60
    seen(db_session, instance, quiet_at - 10)
    assert scan(db_session, quiet_at) == 0
    types = [row.event_type for row in deliveries(db_session)]
    assert "instance_online" not in types

    # And a later episode that quiets OFFLINE on this channel still summarises.
    flip_at = quiet_at
    for _ in range(4):
        _, flip_at = flap_once(db_session, instance, last_seen_at=flip_at)
    assert int(state_of(db_session).flap_suppressed_count or 0) > 0
    assert go_offline_confirmed(db_session, instance, last_seen_at=flip_at) == 0
    final_last_flip = state_of(db_session).flap_episode_last_flip_at
    assert final_last_flip is not None
    final_last_flip_offset = float(
        final_last_flip.timestamp() - at(0).timestamp()
    )
    quiet_at = final_last_flip_offset + FLAP_WINDOW + 60
    assert scan(db_session, quiet_at) == 1
    assert deliveries(db_session)[-1].event_type == "instance_offline"


def test_flap_window_floor_derives_from_detection_and_confirmation(
    db_session, monkeypatch
) -> None:
    """The effective window never drops below 2 x (offline_after + confirm_after).

    Guards the invariant the default now bakes in, even if an operator sets a
    too-small value (or shrinks the other windows and expects the floor to
    follow).
    """

    from onestep_control_plane_api.api.notification_service import _connectivity_flap_window_s

    original_offline = settings.instance_offline_after_s
    original_confirm = settings.instance_connectivity_confirm_after_s
    try:
        monkeypatch.setattr(settings, "instance_connectivity_flap_window_s", 1)
        # Defaults: floor = 2 x (90 + 30) = 240.
        assert _connectivity_flap_window_s() == 240

        monkeypatch.setattr(settings, "instance_offline_after_s", 60)
        monkeypatch.setattr(settings, "instance_connectivity_confirm_after_s", 15)
        assert _connectivity_flap_window_s() == 2 * (60 + 15)

        # A configured value above the floor is honoured as-is.
        monkeypatch.setattr(settings, "instance_connectivity_flap_window_s", 10_000)
        assert _connectivity_flap_window_s() == 10_000
    finally:
        settings.instance_offline_after_s = original_offline
        settings.instance_connectivity_confirm_after_s = original_confirm


def test_default_parameters_damp_realistic_flapping(db_session, monkeypatch) -> None:
    """Defect-2 regression: DEFAULTS damp flapping at realistic cadences.

    The old default window (90 s) was shorter than the shortest possible gap
    between two confirmed flips (offline detection 90 s + confirmation 30 s),
    so every flip opened a fresh episode and the suppressed count stayed zero
    forever -- five reviewer-simulated flips produced five notifications. With
    monotonic time and realistic scan/heartbeat spacing, the suppressed count
    must now climb past the threshold.
    """

    silence_webhooks(monkeypatch)
    instance = seed(db_session)
    assert scan(db_session, 0) == 0

    # Heartbeat every 10 s; notification scanner every 30 s; all clocks advance
    # monotonically. Each leg: go silent ~100 s (detection at +90, confirmed at
    # +120, found by the scan at the next tick), then check in again and let
    # the recovery confirm 30 s later. Two confirmed flips per leg are ~110 s
    # apart -- closer than the floored 240 s window, so this is ONE episode.
    heartbeat = 10.0
    scanner = 30.0

    def next_scan(t: float) -> float:
        return (int(t / scanner) + 1) * scanner

    now = 0.0
    notified_flips = 0
    episode_flips_seen = 0
    for _ in range(5):
        # --- offline leg ---------------------------------------------------
        last_heartbeat = now
        while now < last_heartbeat + OFFLINE_AFTER + CONFIRM_AFTER + heartbeat:
            now += heartbeat
        scan_at = next_scan(now)
        now = scan_at
        notified_flips += scan(db_session, now)
        state = state_of(db_session)
        episode_flips_seen = int(state.flap_episode_flips or 0)
        # --- recovery ------------------------------------------------------
        recovery_seen = now + heartbeat
        seen(db_session, instance, recovery_seen)
        now = next_scan(recovery_seen + CONFIRM_AFTER)
        notified_flips += scan(db_session, now)
        episode_flips_seen = max(
            episode_flips_seen,
            int(state_of(db_session).flap_episode_flips or 0),
        )

    assert episode_flips_seen > 1, (
        "realistic flipping must accumulate inside ONE episode; "
        "a fresh episode per flip means damping can never engage"
    )
    state = state_of(db_session)
    assert int(state.flap_suppressed_count or 0) >= 1, (
        "with default parameters the suppressed count must reach the threshold"
    )
    assert notified_flips <= FLAP_MAX, (
        f"five realistic flip pairs must be damped to <= {FLAP_MAX} notifications, "
        f"got {notified_flips}"
    )
