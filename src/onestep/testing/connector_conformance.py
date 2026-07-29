from __future__ import annotations

import asyncio
import contextlib
import copy
import inspect
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any

from onestep.app import OneStepApp
from onestep.connectors.base import Delivery, Sink, Source
from onestep.envelope import Envelope


class ConnectorCapability(str, Enum):
    BASIC_SOURCE = "basic_source"
    CHECKPOINT_SOURCE = "checkpoint_source"
    CLAIMED_SOURCE = "claimed_source"
    ACKNOWLEDGED_SINK = "acknowledged_sink"
    CHUNKED_SINK = "chunked_sink"
    REPLAY_SAFE_SINK = "replay_safe_sink"
    PUBLIC_ERRORS = "public_errors"


_CAPABILITY_DEPENDENCIES = {
    ConnectorCapability.CHECKPOINT_SOURCE: frozenset({ConnectorCapability.BASIC_SOURCE}),
    ConnectorCapability.CLAIMED_SOURCE: frozenset({ConnectorCapability.BASIC_SOURCE}),
    ConnectorCapability.CHUNKED_SINK: frozenset({ConnectorCapability.ACKNOWLEDGED_SINK}),
    ConnectorCapability.REPLAY_SAFE_SINK: frozenset({ConnectorCapability.ACKNOWLEDGED_SINK}),
}


@dataclass(frozen=True)
class ConnectorConformanceProfile:
    """Test-side declaration of connector capabilities and their evidence."""

    name: str
    contracts: Mapping[ConnectorCapability, tuple[str, ...]]

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("connector profile name must not be empty")
        if not isinstance(self.contracts, Mapping) or not self.contracts:
            raise ValueError("connector profile contracts must not be empty")

        normalized: dict[ConnectorCapability, tuple[str, ...]] = {}
        for raw_capability, raw_contract_ids in self.contracts.items():
            try:
                capability = ConnectorCapability(raw_capability)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"unknown connector capability: {raw_capability!r}") from exc

            if isinstance(raw_contract_ids, str):
                contract_ids = (raw_contract_ids,)
            else:
                contract_ids = tuple(raw_contract_ids)
            if not contract_ids or any(
                not isinstance(contract_id, str) or not contract_id.strip()
                for contract_id in contract_ids
            ):
                raise ValueError(f"{capability.value} must name at least one contract test")
            if len(set(contract_ids)) != len(contract_ids):
                raise ValueError(f"{capability.value} contract test IDs must be unique")
            normalized[capability] = contract_ids

        capabilities = frozenset(normalized)
        for capability, dependencies in _CAPABILITY_DEPENDENCIES.items():
            if capability not in capabilities:
                continue
            missing = dependencies - capabilities
            if missing:
                names = ", ".join(sorted(item.value for item in missing))
                raise ValueError(f"{capability.value} requires: {names}")

        object.__setattr__(self, "name", self.name.strip())
        object.__setattr__(self, "contracts", MappingProxyType(normalized))

    @property
    def capabilities(self) -> frozenset[ConnectorCapability]:
        return frozenset(self.contracts)

    def supports(self, capability: ConnectorCapability) -> bool:
        return capability in self.contracts

    def contract_ids(self, capability: ConnectorCapability) -> tuple[str, ...]:
        try:
            return self.contracts[capability]
        except KeyError as exc:
            raise ValueError(
                f"connector profile {self.name!r} does not declare {capability.value}"
            ) from exc


class StopControl(str, Enum):
    DRAIN = "drain"
    PAUSE = "pause"
    SHUTDOWN = "shutdown"


_Action = Callable[[], object]


@dataclass(frozen=True)
class ClaimedSourceHarness:
    source: Source
    wait_for_fetch_started: _Action
    release_fetch: _Action
    assert_released: _Action


@dataclass(frozen=True)
class AcknowledgedSinkHarness:
    sink: Sink
    wait_for_send_started: _Action
    release_send: _Action


@dataclass(frozen=True)
class ReplaySafeSinkHarness:
    sink: Sink
    assert_single_record: _Action


async def run_claimed_source_stop_contract(
    harness: ClaimedSourceHarness,
    control: StopControl,
    *,
    timeout_s: float = 2.0,
) -> None:
    """Prove a claimed delivery is released when intake stops before handling."""

    if harness.source.fetch_is_cancel_safe:
        raise AssertionError("claimed source must set fetch_is_cancel_safe = False")

    app = OneStepApp(f"claimed-source-{control.value}-contract", shutdown_timeout_s=timeout_s)
    handled: list[Any] = []

    @app.task(source=harness.source, name="connector_contract", concurrency=1)
    async def consume(ctx, item):
        handled.append(item)

    serving = asyncio.create_task(app.serve())
    fetch_released = False
    try:
        await asyncio.wait_for(_invoke(harness.wait_for_fetch_started), timeout=timeout_s)

        if control is StopControl.DRAIN:
            app.request_drain()
        elif control is StopControl.PAUSE:
            app.request_task_pause("connector_contract")
        elif control is StopControl.SHUTDOWN:
            app.request_shutdown()
        else:
            raise ValueError(f"unsupported stop control: {control!r}")

        await asyncio.sleep(0)
        if serving.done():
            raise AssertionError("worker stopped before the non-cancel-safe fetch completed")

        await _invoke(harness.release_fetch)
        fetch_released = True

        if control is StopControl.DRAIN:
            status = await asyncio.wait_for(app.wait_for_drain(), timeout=timeout_s)
            if not status["drained"]:
                raise AssertionError("worker did not drain after releasing the claimed delivery")
            app.request_shutdown()
        elif control is StopControl.PAUSE:
            status = await asyncio.wait_for(
                app.wait_for_task_pause("connector_contract"), timeout=timeout_s
            )
            if not status["paused"]:
                raise AssertionError("task did not pause after releasing the claimed delivery")
            app.request_shutdown()

        await asyncio.wait_for(serving, timeout=timeout_s)
        if handled:
            raise AssertionError(f"claimed deliveries reached the handler after {control.value}")
        await _invoke(harness.assert_released)
    finally:
        if not fetch_released:
            with contextlib.suppress(Exception):
                await _invoke(harness.release_fetch)
        app.request_shutdown()
        if not serving.done():
            serving.cancel()
        await asyncio.gather(serving, return_exceptions=True)


async def run_acknowledged_sink_contract(
    harness: AcknowledgedSinkHarness,
    *,
    body: Any,
    timeout_s: float = 2.0,
) -> None:
    """Prove runtime ack happens only after the sink's backend ack returns."""

    delivery = _ContractDelivery(Envelope(body=body))
    source = _OneShotSource(delivery)
    app = OneStepApp("acknowledged-sink-contract", shutdown_timeout_s=timeout_s)

    @app.task(source=source, emit=harness.sink, concurrency=1)
    async def forward(ctx, item):
        ctx.app.request_shutdown()
        return item

    serving = asyncio.create_task(app.serve())
    send_released = False
    try:
        await asyncio.wait_for(_invoke(harness.wait_for_send_started), timeout=timeout_s)
        if delivery.acked:
            raise AssertionError("delivery was acked before the sink backend acknowledged the send")
        if serving.done():
            raise AssertionError("worker stopped before the sink backend acknowledged the send")

        await _invoke(harness.release_send)
        send_released = True
        await asyncio.wait_for(serving, timeout=timeout_s)

        if not delivery.acked:
            raise AssertionError("delivery was not acked after the sink backend acknowledged the send")
        if delivery.retried or delivery.failed:
            raise AssertionError("successful acknowledged sink send entered a failure path")
    finally:
        if not send_released:
            with contextlib.suppress(Exception):
                await _invoke(harness.release_send)
        app.request_shutdown()
        if not serving.done():
            serving.cancel()
        await asyncio.gather(serving, return_exceptions=True)


async def run_replay_safe_sink_contract(
    harness: ReplaySafeSinkHarness,
    *,
    body: Any,
) -> None:
    """Prove replaying the same logical write leaves one backend record."""

    await harness.sink.open()
    try:
        await harness.sink.send(Envelope(body=copy.deepcopy(body)))
        await harness.sink.send(Envelope(body=copy.deepcopy(body)))
        await _invoke(harness.assert_single_record)
    finally:
        await harness.sink.close()


async def _invoke(action: _Action) -> None:
    result = action()
    if inspect.isawaitable(result):
        await result


class _ContractDelivery(Delivery):
    def __init__(self, envelope: Envelope) -> None:
        super().__init__(envelope)
        self.acked = False
        self.retried = False
        self.failed = False

    async def ack(self) -> None:
        self.acked = True

    async def retry(self, *, delay_s: float | None = None) -> None:
        self.retried = True

    async def fail(self, exc: Exception | None = None) -> None:
        self.failed = True


class _OneShotSource(Source):
    poll_interval_s = 0.01

    def __init__(self, delivery: Delivery) -> None:
        super().__init__("connector-contract-source")
        self.delivery = delivery
        self.sent = False

    async def fetch(self, limit: int) -> list[Delivery]:
        if self.sent:
            return []
        self.sent = True
        return [self.delivery]
