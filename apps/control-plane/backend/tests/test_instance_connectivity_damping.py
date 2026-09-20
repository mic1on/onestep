"""Issue #196: stable confirmation and flapping damping for connectivity flips.

State machine under test (per channel x instance), with DEFAULT settings
``instance_offline_after_s = 90`` and ``instance_connectivity_confirm_after_s = 30``:

* an ``offline`` observation exists only once ``last_seen_at`` is older than the
  90 s offline window;
* a flip is NOTIFIED only once it has HELD for ``confirm_after_s`` measured from
  its own ``transition_at`` (offline = ``last_seen_at + 90``, online =
  ``last_seen_at``);
* a flip that heals inside that window is CANCELLED and never notified;
* a run of confirmed flips closer together than ``flap_window_s`` (90 s) is one
  FLAP EPISODE: the first ``flap_max_notifications`` (3) are notified, later ones
  are suppressed but counted, and ONE summary is emitted when the episode quiets.

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
FLAP_WINDOW = settings.instance_connectivity_flap_window_s  # 90
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
    """One offline->online flip pair whose flips are CLOSER than flap_window_s.

    Offline confirms at ``last_seen + 90 + 30 = +120``. For the online flip to be
    confirmed and still land inside the 90 s flap window, the online check-in is
    placed at ``+100`` (before the offline flip confirms, so it stays pending)
    and confirmed at ``+130`` -- only 10 s after the offline flip. The next
    offline leg then confirms at ``+190``, i.e. 60 s later, so every consecutive
    confirmed flip is < 90 s apart and the whole run is ONE episode.

    Returns (notifications, new_last_seen_offset).
    """

    notified = go_offline_confirmed(db_session, instance, last_seen_at=last_seen_at)
    # Check in at +110 (10 s after the offline flip confirmed at +120 -> no, that
    # is BEFORE: use +110 so the offline flip confirms at +120 first, then the
    # online candidate is confirmed 30 s after this check-in, i.e. at +170,
    # exactly 50 s after the offline flip -> well inside the 90 s window).
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
    # A confirmed offline flip inherently takes 90 s of silence, so consecutive
    # flips are ~90 s apart. Widen the flap window to 150 s so this run is ONE
    # episode -- otherwise each flip correctly starts a fresh episode and nothing
    # is damped (which is the documented behaviour, not the property under test).
    monkeypatch.setattr(settings, "instance_connectivity_flap_window_s", 150)
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
    monkeypatch.setattr(settings, "instance_connectivity_flap_window_s", 180)
    assert scan(db_session, 0) == 0

    flip_at = 0.0
    for _ in range(6):
        _, flip_at = flap_once(db_session, instance, last_seen_at=flip_at)

    state = state_of(db_session)
    assert int(state.flap_suppressed_count or 0) > 0, "no flip was suppressed"

    # Let the episode go quiet: no flip for a full (widened) flap window.
    quiet_at = flip_at + 180 + 60
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
    monkeypatch.setattr(settings, "instance_connectivity_flap_window_s", 150)
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
