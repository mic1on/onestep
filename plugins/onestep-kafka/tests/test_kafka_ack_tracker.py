from __future__ import annotations

from onestep_kafka import KafkaOffsetTracker


def test_tracker_commits_contiguous_offsets_in_order() -> None:
    tracker = KafkaOffsetTracker()
    tracker.mark_fetched("orders", 0, 10)
    tracker.mark_fetched("orders", 0, 11)

    assert tracker.mark_completed("orders", 0, 10) == 11
    tracker.mark_committed("orders", 0, 11)
    assert tracker.mark_completed("orders", 0, 11) == 12
    tracker.mark_committed("orders", 0, 12)
    assert tracker.next_committed_offset("orders", 0) == 12


def test_tracker_does_not_commit_past_gap() -> None:
    tracker = KafkaOffsetTracker()
    tracker.mark_fetched("orders", 0, 10)
    tracker.mark_fetched("orders", 0, 11)

    assert tracker.mark_completed("orders", 0, 11) is None
    assert tracker.mark_completed("orders", 0, 10) == 12


def test_tracker_keeps_partitions_independent() -> None:
    tracker = KafkaOffsetTracker()
    tracker.mark_fetched("orders", 0, 10)
    tracker.mark_fetched("orders", 1, 5)

    assert tracker.mark_completed("orders", 1, 5) == 6
    assert tracker.mark_completed("orders", 0, 10) == 11


def test_tracker_release_unstarted_does_not_commit() -> None:
    tracker = KafkaOffsetTracker()
    tracker.mark_fetched("orders", 0, 10)

    assert tracker.mark_released_unstarted("orders", 0, 10) == 10
    assert tracker.next_committed_offset("orders", 0) == 10
    assert tracker.mark_completed("orders", 0, 10) == 11


def test_tracker_does_not_release_started_delivery() -> None:
    tracker = KafkaOffsetTracker()
    tracker.mark_fetched("orders", 0, 10)
    tracker.mark_started("orders", 0, 10)

    assert tracker.mark_released_unstarted("orders", 0, 10) is None
    assert tracker.mark_completed("orders", 0, 10) == 11
