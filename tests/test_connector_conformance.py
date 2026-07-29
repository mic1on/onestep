from __future__ import annotations

import asyncio
import threading

import pytest

from onestep import Delivery, Envelope, Sink, Source
from onestep.testing import (
    AcknowledgedSinkHarness,
    ClaimedSourceHarness,
    ConnectorCapability,
    ConnectorConformanceProfile,
    ReplaySafeSinkHarness,
    StopControl,
    run_acknowledged_sink_contract,
    run_claimed_source_stop_contract,
    run_replay_safe_sink_contract,
)


def test_profile_normalizes_and_freezes_contract_evidence() -> None:
    contracts = {
        ConnectorCapability.BASIC_SOURCE: ("test_fetch",),
        ConnectorCapability.CHECKPOINT_SOURCE: ("test_ack_checkpoint",),
    }

    profile = ConnectorConformanceProfile(name="  example  ", contracts=contracts)
    contracts[ConnectorCapability.BASIC_SOURCE] = ("changed",)

    assert profile.name == "example"
    assert profile.capabilities == {
        ConnectorCapability.BASIC_SOURCE,
        ConnectorCapability.CHECKPOINT_SOURCE,
    }
    assert profile.contract_ids(ConnectorCapability.BASIC_SOURCE) == ("test_fetch",)
    assert profile.supports(ConnectorCapability.CHECKPOINT_SOURCE) is True
    assert profile.supports(ConnectorCapability.PUBLIC_ERRORS) is False


def test_profile_normalizes_a_single_contract_id() -> None:
    profile = ConnectorConformanceProfile(
        name="example",
        contracts={ConnectorCapability.PUBLIC_ERRORS: "test_public_error"},
    )

    assert profile.contract_ids(ConnectorCapability.PUBLIC_ERRORS) == ("test_public_error",)


@pytest.mark.parametrize("name", [None, "", "   "])
def test_profile_rejects_invalid_names(name) -> None:
    with pytest.raises(ValueError, match="name must not be empty"):
        ConnectorConformanceProfile(
            name=name,
            contracts={ConnectorCapability.PUBLIC_ERRORS: ("test_error",)},
        )


def test_profile_rejects_unknown_capabilities() -> None:
    with pytest.raises(ValueError, match="unknown connector capability"):
        ConnectorConformanceProfile(
            name="invalid",
            contracts={"future_capability": ("test_future",)},
        )


@pytest.mark.parametrize(
    ("capability", "dependency"),
    [
        (ConnectorCapability.CHECKPOINT_SOURCE, ConnectorCapability.BASIC_SOURCE),
        (ConnectorCapability.CLAIMED_SOURCE, ConnectorCapability.BASIC_SOURCE),
        (ConnectorCapability.CHUNKED_SINK, ConnectorCapability.ACKNOWLEDGED_SINK),
        (ConnectorCapability.REPLAY_SAFE_SINK, ConnectorCapability.ACKNOWLEDGED_SINK),
    ],
)
def test_profile_rejects_missing_capability_dependencies(capability, dependency) -> None:
    with pytest.raises(ValueError, match=f"{capability.value} requires: {dependency.value}"):
        ConnectorConformanceProfile(name="invalid", contracts={capability: ("test_case",)})


@pytest.mark.parametrize(
    "contracts",
    [
        None,
        [],
        {},
        {ConnectorCapability.PUBLIC_ERRORS: ()},
        {ConnectorCapability.PUBLIC_ERRORS: ("",)},
        {ConnectorCapability.PUBLIC_ERRORS: ("test_error", "test_error")},
    ],
)
def test_profile_rejects_missing_or_invalid_contract_evidence(contracts) -> None:
    with pytest.raises(ValueError):
        ConnectorConformanceProfile(name="invalid", contracts=contracts)


def test_profile_reports_undeclared_capability() -> None:
    profile = ConnectorConformanceProfile(
        name="errors-only",
        contracts={ConnectorCapability.PUBLIC_ERRORS: ("test_errors",)},
    )

    with pytest.raises(ValueError, match="does not declare basic_source"):
        profile.contract_ids(ConnectorCapability.BASIC_SOURCE)


@pytest.mark.parametrize("control", list(StopControl))
def test_claimed_source_runner_covers_each_stop_control(control: StopControl) -> None:
    async def scenario() -> None:
        source = _BlockingClaimedSource()

        def assert_released() -> None:
            assert source.delivery.released is True
            assert source.delivery.acked is False
            assert source.delivery.retried is False
            assert source.delivery.failed is False

        await run_claimed_source_stop_contract(
            ClaimedSourceHarness(
                source=source,
                wait_for_fetch_started=source.fetch_started.wait,
                release_fetch=source.release_fetch.set,
                assert_released=assert_released,
            ),
            control,
        )

    asyncio.run(scenario())


def test_claimed_source_runner_rejects_cancel_safe_source() -> None:
    source = _CancelSafeSource("cancel-safe")

    with pytest.raises(AssertionError, match="fetch_is_cancel_safe"):
        asyncio.run(
            run_claimed_source_stop_contract(
                ClaimedSourceHarness(
                    source=source,
                    wait_for_fetch_started=lambda: None,
                    release_fetch=lambda: None,
                    assert_released=lambda: None,
                ),
                StopControl.SHUTDOWN,
            )
        )


def test_claimed_source_runner_times_out_a_blocking_sync_wait_callback() -> None:
    async def scenario() -> None:
        source = _BlockingClaimedSource()
        callback_gate = threading.Event()

        try:
            with pytest.raises(TimeoutError):
                await run_claimed_source_stop_contract(
                    ClaimedSourceHarness(
                        source=source,
                        wait_for_fetch_started=callback_gate.wait,
                        release_fetch=source.release_fetch.set,
                        assert_released=lambda: None,
                    ),
                    StopControl.SHUTDOWN,
                    timeout_s=0.01,
                )
        finally:
            callback_gate.set()

    asyncio.run(scenario())


def test_acknowledged_sink_runner_enforces_runtime_ack_ordering() -> None:
    async def scenario() -> None:
        sink = _BlockingSink()
        await run_acknowledged_sink_contract(
            AcknowledgedSinkHarness(
                sink=sink,
                wait_for_send_started=sink.send_started.wait,
                release_send=sink.release_send.set,
            ),
            body={"id": 1},
        )
        assert sink.items == [{"id": 1}]

    asyncio.run(scenario())


def test_replay_safe_sink_runner_sends_twice_and_requires_one_record() -> None:
    async def scenario() -> None:
        sink = _ReplaySafeSink()

        def assert_single_record() -> None:
            assert sink.records == {"event-1": {"id": "event-1", "value": 3}}
            assert sink.send_calls == 2

        await run_replay_safe_sink_contract(
            ReplaySafeSinkHarness(
                sink=sink,
                assert_single_record=assert_single_record,
            ),
            body={"id": "event-1", "value": 3},
        )

    asyncio.run(scenario())


def test_replay_safe_sink_runner_closes_after_a_failed_backend_assertion() -> None:
    async def scenario() -> None:
        sink = _ReplaySafeSink()

        def reject_duplicate() -> None:
            raise AssertionError("backend retained two logical records")

        with pytest.raises(AssertionError, match="retained two logical records"):
            await run_replay_safe_sink_contract(
                ReplaySafeSinkHarness(
                    sink=sink,
                    assert_single_record=reject_duplicate,
                ),
                body={"id": "event-1", "value": 3},
            )

        assert sink.closed is True

    asyncio.run(scenario())


class _RecordingDelivery(Delivery):
    def __init__(self) -> None:
        super().__init__(Envelope(body={"id": 1}))
        self.released = False
        self.acked = False
        self.retried = False
        self.failed = False

    async def release_unstarted(self) -> None:
        self.released = True

    async def ack(self) -> None:
        self.acked = True

    async def retry(self, *, delay_s: float | None = None) -> None:
        self.retried = True

    async def fail(self, exc: Exception | None = None) -> None:
        self.failed = True


class _BlockingClaimedSource(Source):
    fetch_is_cancel_safe = False
    poll_interval_s = 0.01

    def __init__(self) -> None:
        super().__init__("blocking-claimed-source")
        self.fetch_started = asyncio.Event()
        self.release_fetch = asyncio.Event()
        self.delivery = _RecordingDelivery()

    async def fetch(self, limit: int) -> list[Delivery]:
        self.fetch_started.set()
        await self.release_fetch.wait()
        return [self.delivery]


class _BlockingSink(Sink):
    def __init__(self) -> None:
        super().__init__("blocking-acknowledged-sink")
        self.send_started = asyncio.Event()
        self.release_send = asyncio.Event()
        self.items: list[object] = []

    async def send(self, envelope: Envelope) -> None:
        self.send_started.set()
        await self.release_send.wait()
        self.items.append(envelope.body)


class _CancelSafeSource(Source):
    async def fetch(self, limit: int) -> list[Delivery]:
        return []


class _ReplaySafeSink(Sink):
    def __init__(self) -> None:
        super().__init__("replay-safe-sink")
        self.records: dict[str, dict[str, object]] = {}
        self.send_calls = 0
        self.closed = False

    async def send(self, envelope: Envelope) -> None:
        self.send_calls += 1
        self.records[envelope.body["id"]] = dict(envelope.body)

    async def close(self) -> None:
        self.closed = True
