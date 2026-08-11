from __future__ import annotations

import asyncio
import copy
import inspect
import logging
from typing import TYPE_CHECKING, Any

from onestep.events import TaskEvent, TaskEventKind
from onestep.resilience import (
    ConnectorOperationError,
    connector_retry_delay,
    is_retryable_connector_error,
)
from onestep.task import TaskSpec

from .executor import DeliveryExecutor

if TYPE_CHECKING:
    from onestep.app import OneStepApp
    from onestep.connectors.base import Delivery


class TaskRunner:
    def __init__(self, app: "OneStepApp", task: TaskSpec) -> None:
        self.app = app
        self.task = task
        self._inflight: set[asyncio.Task[None]] = set()
        self._fetching = False
        self._drain_parked = False
        self._pause_parked = False
        self._logger = logging.getLogger(f"onestep.{app.name}.{task.name}")
        self._executor = DeliveryExecutor(app, task)

    @property
    def inflight_count(self) -> int:
        return len(self._inflight)

    @property
    def is_fetching(self) -> bool:
        return self._fetching

    @property
    def is_drain_parked(self) -> bool:
        return self._drain_parked

    @property
    def is_pause_parked(self) -> bool:
        return self._pause_parked

    async def run(self) -> None:
        if self.task.source is None:
            return
        try:
            while not self.app.is_stopping:
                if self.app.is_draining:
                    if self._inflight:
                        await self._wait_for_inflight(timeout=self.task.source.poll_interval_s)
                        continue
                    self._set_pause_parked(False)
                    self._set_drain_parked(True)
                    await self.app.wait_for_shutdown()
                    break

                if self.app.is_task_paused(self.task.name):
                    if self._inflight:
                        await self._wait_for_inflight(timeout=self.task.source.poll_interval_s)
                        continue
                    self._set_drain_parked(False)
                    self._set_pause_parked(True)
                    try:
                        await asyncio.wait_for(
                            self.app.wait_for_shutdown(),
                            timeout=self.task.source.poll_interval_s,
                        )
                    except asyncio.TimeoutError:
                        continue
                    break

                resumed_from_pause = self._pause_parked
                self._set_drain_parked(False)
                if resumed_from_pause:
                    await self._resume_source_after_pause()
                self._set_pause_parked(False)
                available = self.task.concurrency - len(self._inflight)
                if available <= 0:
                    await self._wait_for_inflight(timeout=self.task.source.poll_interval_s)
                    continue

                deliveries = await self._fetch_deliveries(available)
                if not deliveries:
                    if self.app.is_stopping:
                        break
                    if self._inflight:
                        await self._wait_for_inflight(timeout=self.task.source.poll_interval_s)
                    else:
                        await asyncio.sleep(self.task.source.poll_interval_s)
                    continue
                await self._emit_batch_event(TaskEventKind.FETCHED, deliveries)

                for delivery in deliveries:
                    pending = asyncio.create_task(self._handle_delivery(delivery))
                    self._track_inflight(pending)
        finally:
            self._set_drain_parked(False)
            self._set_pause_parked(False)
            self._set_fetching(False)
            await self._drain_inflight()

    async def _wait_for_inflight(self, timeout: float | None) -> None:
        if not self._inflight:
            return
        await asyncio.wait(self._inflight, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)

    async def _fetch_deliveries(self, limit: int) -> list["Delivery"]:
        if (
            self.task.source is None
            or self.app.is_stopping
            or self.app.is_draining
            or self.app.is_task_paused(self.task.name)
        ):
            return []
        self._set_fetching(True)
        fetch_task = asyncio.create_task(self.task.source.fetch(limit))
        stop_fetching_task = asyncio.create_task(self.app.wait_for_stop_fetching(self.task.name))
        pending: set[asyncio.Task[Any]] = {fetch_task, stop_fetching_task}
        try:
            done, pending = await asyncio.wait(
                pending,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if stop_fetching_task in done:
                if self.task.source.fetch_is_cancel_safe:
                    fetch_task.cancel()
                    await asyncio.gather(fetch_task, return_exceptions=True)
                else:
                    deliveries = await self._resolve_fetch_task(fetch_task)
                    await self._release_unstarted_deliveries(deliveries)
                return []
            if fetch_task in done:
                deliveries = await self._resolve_fetch_task(fetch_task)
                if self.app.is_stopping or self.app.is_draining or self.app.is_task_paused(self.task.name):
                    await self._release_unstarted_deliveries(deliveries)
                    return []
                return deliveries
            fetch_task.cancel()
            await asyncio.gather(fetch_task, return_exceptions=True)
            return []
        except asyncio.CancelledError:
            await self._cancel_or_release_fetch(fetch_task)
            raise
        finally:
            self._set_fetching(False)
            for pending_task in pending:
                pending_task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    async def _resolve_fetch_task(self, fetch_task: asyncio.Task[list["Delivery"]]) -> list["Delivery"]:
        try:
            return await fetch_task
        except ConnectorOperationError as exc:
            if not is_retryable_connector_error(exc):
                raise
            await self._handle_source_fetch_error(exc)
            return []

    async def _cancel_or_release_fetch(self, fetch_task: asyncio.Task[list["Delivery"]]) -> None:
        assert self.task.source is not None
        if not fetch_task.done() and self.task.source.fetch_is_cancel_safe:
            fetch_task.cancel()
            await asyncio.gather(fetch_task, return_exceptions=True)
            return
        deliveries = await self._resolve_fetch_task(fetch_task)
        await self._release_unstarted_deliveries(deliveries)

    async def _release_unstarted_deliveries(self, deliveries: list["Delivery"]) -> None:
        for delivery in deliveries:
            try:
                await delivery.release_unstarted()
            except Exception:
                self._logger.exception("releasing unstarted delivery failed")

    def _track_inflight(self, task: asyncio.Task[None]) -> None:
        self._inflight.add(task)
        self.app.notify_runner_state_changed()
        task.add_done_callback(self._handle_inflight_done)

    def _handle_inflight_done(self, task: asyncio.Task[None]) -> None:
        self._inflight.discard(task)
        self.app.notify_runner_state_changed()

    def _set_fetching(self, value: bool) -> None:
        if self._fetching == value:
            return
        self._fetching = value
        self.app.notify_runner_state_changed()

    def _set_drain_parked(self, value: bool) -> None:
        if self._drain_parked == value:
            return
        self._drain_parked = value
        self.app.notify_runner_state_changed()

    def _set_pause_parked(self, value: bool) -> None:
        if self._pause_parked == value:
            return
        self._pause_parked = value
        self.app.notify_runner_state_changed()

    async def _resume_source_after_pause(self) -> None:
        if self.task.source is None:
            return
        hook = getattr(self.task.source, "resume_after_pause", None)
        if not callable(hook):
            return
        result = hook()
        if inspect.isawaitable(result):
            await result

    async def _handle_source_fetch_error(self, exc: ConnectorOperationError) -> None:
        fallback_s = self.task.source.poll_interval_s if self.task.source is not None else 1.0
        delay_s = connector_retry_delay(exc, fallback_s=fallback_s)
        self._logger.warning(
            "source fetch degraded; backing off",
            extra={
                "connector_backend": exc.backend,
                "connector_operation": exc.operation.value,
                "connector_kind": exc.kind.value,
                "connector_retry_delay_s": delay_s,
            },
            exc_info=exc,
        )
        if delay_s <= 0 or self.app.is_stopping:
            return
        try:
            await asyncio.wait_for(self.app.wait_for_shutdown(), timeout=delay_s)
        except asyncio.TimeoutError:
            return

    async def _handle_delivery(self, delivery: "Delivery") -> None:
        await self._executor.execute(delivery)

    async def _drain_inflight(self) -> None:
        if not self._inflight:
            return
        if self.app.shutdown_timeout_s is None:
            await asyncio.gather(*self._inflight, return_exceptions=True)
            return
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.app.shutdown_timeout_s
        done, pending = await asyncio.wait(
            self._inflight,
            timeout=self.app.shutdown_timeout_s,
        )
        if done:
            await asyncio.gather(*done, return_exceptions=True)
        if pending:
            for pending_task in pending:
                pending_task.cancel()
            remaining = max(0.0, deadline - loop.time())
            if remaining <= 0:
                self._logger.warning(
                    "inflight delivery cancellation exceeded shutdown timeout"
                )
                return
            try:
                await asyncio.wait_for(
                    asyncio.gather(*pending, return_exceptions=True),
                    timeout=remaining,
                )
            except asyncio.TimeoutError:
                self._logger.warning(
                    "inflight delivery cleanup exceeded shutdown timeout"
                )

    async def _emit_batch_event(self, kind: TaskEventKind, deliveries: list["Delivery"]) -> None:
        for delivery in deliveries:
            await self._emit_event(kind, delivery)

    async def _emit_event(
        self,
        kind: TaskEventKind,
        delivery: "Delivery",
    ) -> None:
        event = TaskEvent(
            kind=kind,
            app=self.app.name,
            task=self.task.name,
            source=self.task.source.name if self.task.source is not None else None,
            attempts=delivery.envelope.attempts,
            meta=copy.deepcopy(delivery.envelope.meta),
        )
        await self.app.emit_event(event)
