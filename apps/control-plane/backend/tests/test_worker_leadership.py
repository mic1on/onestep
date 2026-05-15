from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from onestep_control_plane_api.ops.readiness import (
    BackgroundTaskReadinessState,
    build_default_background_task_states,
)
from onestep_control_plane_api.workers.leader import (
    NullLeader,
    PostgresAdvisoryLeader,
)
from onestep_control_plane_api.workers.notification_scanner import (
    NotificationMissedStartScanner,
)


class TestPostgresAdvisoryLeader:
    def test_initial_state_is_not_leader(self) -> None:
        leader = PostgresAdvisoryLeader(MagicMock())
        assert leader.state.is_leader is False
        assert leader.state.acquired_at is None

    def test_try_acquire_returns_false_when_db_fails(self) -> None:
        session_factory = MagicMock(side_effect=RuntimeError("db unavailable"))
        leader = PostgresAdvisoryLeader(session_factory)
        assert leader.try_acquire() is False
        assert leader.state.is_leader is False

    def test_renew_returns_false_when_not_leader(self) -> None:
        leader = PostgresAdvisoryLeader(MagicMock())
        assert leader.renew() is False

    def test_release_returns_false_when_not_leader(self) -> None:
        leader = PostgresAdvisoryLeader(MagicMock())
        assert leader.release() is False

    def test_is_still_leader_returns_false_when_not_leader(self) -> None:
        leader = PostgresAdvisoryLeader(MagicMock())
        assert leader.is_still_leader() is False


class TestNullLeader:
    def test_always_reports_as_leader(self) -> None:
        leader = NullLeader()
        assert leader.state.is_leader is True
        assert leader.try_acquire() is True
        assert leader.renew() is True
        assert leader.is_still_leader() is True

    def test_release_resets_state(self) -> None:
        leader = NullLeader()
        assert leader.release() is True
        assert leader.state.is_leader is False


class TestNotificationMissedStartScanner:
    def test_readiness_state_is_accessible(self) -> None:
        state = BackgroundTaskReadinessState(name="test_scanner")
        leader = NullLeader()
        scanner = NotificationMissedStartScanner(
            MagicMock(),
            leader,
            state,
            started_at=datetime.now(UTC),
        )
        assert scanner.readiness_state is state
        assert scanner.readiness_state.name == "test_scanner"

    def test_request_shutdown_sets_flag(self) -> None:
        state = BackgroundTaskReadinessState(name="test_scanner")
        leader = NullLeader()
        scanner = NotificationMissedStartScanner(
            MagicMock(),
            leader,
            state,
            started_at=datetime.now(UTC),
        )
        assert scanner._shutdown_requested is False
        scanner.request_shutdown()
        assert scanner._shutdown_requested is True

    def test_run_scan_once_marks_success_on_empty_scan(self) -> None:
        state = BackgroundTaskReadinessState(name="test_scanner")
        leader = NullLeader()
        mock_session = MagicMock()
        mock_session.__enter__.return_value = mock_session

        with patch(
            "onestep_control_plane_api.workers.notification_scanner.scan_and_dispatch_missed_start_notifications",
            return_value=0,
        ):
            scanner = NotificationMissedStartScanner(
                lambda: mock_session,
                leader,
                state,
                started_at=datetime.now(UTC),
            )

            import asyncio
            asyncio.run(scanner._run_scan_once())

        assert state.last_success_at is not None
        assert state.last_error is None

    def test_run_scan_once_marks_failure_on_exception(self) -> None:
        state = BackgroundTaskReadinessState(name="test_scanner")
        leader = NullLeader()
        mock_session = MagicMock()
        mock_session.__enter__.return_value = mock_session

        with patch(
            "onestep_control_plane_api.workers.notification_scanner.scan_and_dispatch_missed_start_notifications",
            side_effect=RuntimeError("scan failed"),
        ):
            scanner = NotificationMissedStartScanner(
                lambda: mock_session,
                leader,
                state,
                started_at=datetime.now(UTC),
            )

            import asyncio
            asyncio.run(scanner._run_scan_once())

        assert state.last_failure_at is not None
        assert state.last_error is not None
        assert "scan failed" in state.last_error


class TestBackgroundTaskReadinessState:
    def test_mark_started_sets_timestamps(self) -> None:
        state = BackgroundTaskReadinessState(name="test")
        now = datetime.now(UTC)
        state.mark_started(now)
        assert state.started_at == now
        assert state.last_tick_at == now

    def test_mark_success_clears_error(self) -> None:
        state = BackgroundTaskReadinessState(name="test")
        state.mark_failure(ValueError("oops"))
        assert state.last_error is not None
        state.mark_success()
        assert state.last_error is None

    def test_build_default_states_includes_scanner(self) -> None:
        states = build_default_background_task_states()
        assert "notification_missed_start_scanner" in states
        assert states["notification_missed_start_scanner"].name == "notification_missed_start_scanner"