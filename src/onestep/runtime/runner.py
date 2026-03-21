from __future__ import annotations

import asyncio
import logging
import copy
import inspect
import time
from typing import TYPE_CHECKING

from onestep.context import TaskContext
from onestep.envelope import Envelope
from onestep.events import TaskEvent, TaskEventKind
from onestep.resilience import ConnectorOperationError, connector_retry_delay, is_retryable_connector_error
from onestep.retry import FailureInfo, FailureKind, RetryDecision, RetryInLocal, resolve_retry_action
from onestep.task import TaskSpec

if TYPE_CHECKING:
    from onestep.app import OneStepApp
    from onestep.connectors.base import Delivery


class TaskRunner:
    _SEND_ATTEMPTS = 2
    def __init__(self, app: "OneStepApp", task: TaskSpec) -> None:
        self.app = app
        self.task = task
        self._inflight: set[asyncio.Task[None]] = set()
        self._fetching = False
        self._drain_parked = False
        self._pause_parked = False
        self._logger = logging.getLogger(f"onestep.{app.name}.{task.name}")

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

                self._set_drain_parked(False)
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
        done, pending = await asyncio.wait(
            {fetch_task, stop_fetching_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        try:
            if stop_fetching_task in done:
                fetch_task.cancel()
                await asyncio.gather(fetch_task, return_exceptions=True)
                return []
            if fetch_task in done:
                try:
                    return fetch_task.result()
                except ConnectorOperationError as exc:
                    if not is_retryable_connector_error(exc):
                        raise
                    await self._handle_source_fetch_error(exc)
                    return []
            fetch_task.cancel()
            await asyncio.gather(fetch_task, return_exceptions=True)
            return []
        finally:
            self._set_fetching(False)
            for pending_task in pending:
                pending_task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

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

    async def _send_to_sink(self, sink, envelope: Envelope) -> None:
        fallback_s = getattr(sink, "poll_interval_s", 1.0)
        for attempt in range(self._SEND_ATTEMPTS):
            try:
                await sink.send(envelope)
                return
            except ConnectorOperationError as exc:
                if not is_retryable_connector_error(exc) or attempt == self._SEND_ATTEMPTS - 1:
                    raise
                delay_s = connector_retry_delay(exc, fallback_s=fallback_s)
                self._logger.warning(
                    "sink send degraded; retrying",
                    extra={
                        "connector_backend": exc.backend,
                        "connector_operation": exc.operation.value,
                        "connector_kind": exc.kind.value,
                        "connector_retry_delay_s": delay_s,
                    },
                    exc_info=exc,
                )
                if delay_s <= 0 or self.app.is_stopping:
                    continue
                try:
                    await asyncio.wait_for(self.app.wait_for_shutdown(), timeout=delay_s)
                except asyncio.TimeoutError:
                    continue
                raise

    async def _handle_delivery(self, delivery: "Delivery") -> None:
        ctx = TaskContext(app=self.app, task=self.task, delivery=delivery)
        started_at = time.perf_counter()
        try:
            await delivery.start_processing()
            await self._emit_event(TaskEventKind.STARTED, delivery)
            result = await self._invoke_handler(ctx, delivery)
            if result is not None and self.task.sinks:
                envelope = Envelope(body=result)
                for sink in self.task.sinks:
                    await self._send_to_sink(sink, envelope)
            await delivery.ack()
            await self._emit_event(
                TaskEventKind.SUCCEEDED,
                delivery,
                duration_s=time.perf_counter() - started_at,
            )
        except asyncio.CancelledError:
            failure = FailureInfo.from_exception(asyncio.CancelledError(), kind=FailureKind.CANCELLED)
            ctx.logger.warning("task cancelled", extra={"failure_kind": failure.kind.value})
            duration_s = time.perf_counter() - started_at
            await self._emit_event(
                TaskEventKind.CANCELLED,
                delivery,
                failure=failure,
                duration_s=duration_s,
            )
            await delivery.retry()
            await self._emit_event(
                TaskEventKind.RETRIED,
                delivery,
                failure=failure,
                duration_s=duration_s,
            )
            raise
        except asyncio.TimeoutError as exc:
            await self._handle_failure(
                ctx,
                delivery,
                exc,
                FailureKind.TIMEOUT,
                duration_s=time.perf_counter() - started_at,
            )
        except Exception as exc:
            await self._handle_failure(
                ctx,
                delivery,
                exc,
                FailureKind.ERROR,
                duration_s=time.perf_counter() - started_at,
            )

    async def _invoke_handler(self, ctx: TaskContext, delivery: "Delivery"):
        result = self.task.handler(ctx, delivery.payload)
        if inspect.isawaitable(result):
            if self.task.timeout_s is not None:
                return await asyncio.wait_for(result, timeout=self.task.timeout_s)
            return await result
        return result

    async def _handle_failure(
        self,
        ctx: TaskContext,
        delivery: "Delivery",
        exc: Exception,
        kind: FailureKind,
        *,
        duration_s: float | None,
    ) -> None:
        failure = FailureInfo.from_exception(exc, kind=kind)
        ctx.logger.exception("task failed", extra={"failure_kind": failure.kind.value})

        # Handle RetryInLocal: retry in-process without broker I/O
        if isinstance(exc, RetryInLocal):
            await self._handle_retry_in_local(ctx, delivery, exc, failure, duration_s=duration_s)
            return

        action = resolve_retry_action(self.task.retry, delivery.envelope, exc, failure)
        if action.decision is RetryDecision.RETRY:
            await delivery.retry(delay_s=action.delay_s)
            await self._emit_event(
                TaskEventKind.RETRIED,
                delivery,
                failure=failure,
                duration_s=duration_s,
            )
            return
        if self.task.dead_letter_sinks:
            published = await self._publish_dead_letter(
                ctx,
                delivery,
                failure,
                duration_s=duration_s,
            )
            if not published:
                return
        await self._fail_delivery(ctx, delivery, exc)
        await self._emit_event(
            TaskEventKind.FAILED,
            delivery,
            failure=failure,
            duration_s=duration_s,
        )

    async def _handle_retry_in_local(
        self,
        ctx: TaskContext,
        delivery: "Delivery",
        exc: RetryInLocal,
        failure: FailureInfo,
        *,
        duration_s: float | None,
    ) -> None:
        """Handle RetryInLocal: retry in-process without broker I/O.

        This implements true local retry by:
        1. Checking the retry policy for permission to retry
        2. If allowed, waiting for the optional delay
        3. Re-invoking the handler directly (no new broker message)
        4. On success, continuing normal flow; on failure, repeating the cycle
        """
        # Check retry policy
        action = resolve_retry_action(self.task.retry, delivery.envelope, exc, failure)

        if action.decision is not RetryDecision.RETRY:
            # No more retries allowed - fall through to failure handling
            if self.task.dead_letter_sinks:
                published = await self._publish_dead_letter(
                    ctx,
                    delivery,
                    failure,
                    duration_s=duration_s,
                )
                if not published:
                    return
            await self._fail_delivery(ctx, delivery, exc)
            await self._emit_event(
                TaskEventKind.FAILED,
                delivery,
                failure=failure,
                duration_s=duration_s,
            )
            return

        # Determine delay: prefer RetryInLocal's delay, fall back to policy's
        delay_s = exc.delay_s if exc.delay_s is not None else action.delay_s

        # Wait if delay is specified
        if delay_s and delay_s > 0:
            ctx.logger.info(f"retrying locally in {delay_s}s")
            try:
                await asyncio.wait_for(self.app.wait_for_shutdown(), timeout=delay_s)
            except asyncio.TimeoutError:
                pass  # Continue with retry
            if self.app.is_stopping:
                return

        # Increment attempts for local retry
        delivery.envelope.attempts += 1

        ctx.logger.info(
            f"retrying locally (attempt {delivery.envelope.attempts})",
            extra={"attempts": delivery.envelope.attempts},
        )

        # Emit retry event
        await self._emit_event(
            TaskEventKind.RETRIED,
            delivery,
            failure=failure,
            duration_s=duration_s,
        )

        # Retry the handler directly in-process
        try:
            await delivery.start_processing()
            result = await self._invoke_handler(ctx, delivery)
            if result is not None and self.task.sinks:
                envelope = Envelope(body=result)
                for sink in self.task.sinks:
                    await self._send_to_sink(sink, envelope)
            await delivery.ack()
            await self._emit_event(
                TaskEventKind.SUCCEEDED,
                delivery,
                duration_s=time.perf_counter() - duration_s if duration_s else 0,
            )
        except Exception as retry_exc:
            # Recursively handle the retry result (could be another RetryInLocal or final failure)
            if isinstance(retry_exc, RetryInLocal):
                await self._handle_retry_in_local(ctx, delivery, retry_exc, failure, duration_s=None)
            else:
                await self._handle_failure(ctx, delivery, retry_exc, FailureKind.ERROR, duration_s=None)

    async def _publish_dead_letter(
        self,
        ctx: TaskContext,
        delivery: "Delivery",
        failure: FailureInfo,
        *,
        duration_s: float | None,
    ) -> bool:
        envelope = Envelope(
            body={
                "payload": copy.deepcopy(delivery.envelope.body),
                "failure": failure.as_dict(),
            },
            meta={
                "app": self.app.name,
                "task": self.task.name,
                "source": self.task.source.name if self.task.source is not None else None,
                "original_meta": copy.deepcopy(delivery.envelope.meta),
                "original_attempts": delivery.envelope.attempts,
            },
            attempts=0,
        )
        try:
            for sink in self.task.dead_letter_sinks:
                await sink.send(envelope)
        except Exception:
            ctx.logger.exception("dead-letter publish failed; retrying original delivery")
            await delivery.retry()
            await self._emit_event(
                TaskEventKind.RETRIED,
                delivery,
                failure=failure,
                duration_s=duration_s,
            )
            return False
        await self._emit_event(
            TaskEventKind.DEAD_LETTERED,
            delivery,
            failure=failure,
            duration_s=duration_s,
        )
        return True

    async def _fail_delivery(self, ctx: TaskContext, delivery: "Delivery", exc: Exception) -> None:
        try:
            await delivery.fail(exc)
        except Exception:
            ctx.logger.exception("delivery fail action failed; retrying original delivery")
            await delivery.retry()

    async def _drain_inflight(self) -> None:
        if not self._inflight:
            return
        if self.app.shutdown_timeout_s is None:
            await asyncio.gather(*self._inflight, return_exceptions=True)
            return
        done, pending = await asyncio.wait(self._inflight, timeout=self.app.shutdown_timeout_s)
        if done:
            await asyncio.gather(*done, return_exceptions=True)
        if pending:
            for pending_task in pending:
                pending_task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    async def _emit_batch_event(self, kind: TaskEventKind, deliveries: list["Delivery"]) -> None:
        for delivery in deliveries:
            await self._emit_event(kind, delivery)

    async def _emit_event(
        self,
        kind: TaskEventKind,
        delivery: "Delivery",
        *,
        duration_s: float | None = None,
        failure: FailureInfo | None = None,
    ) -> None:
        event = TaskEvent(
            kind=kind,
            app=self.app.name,
            task=self.task.name,
            source=self.task.source.name if self.task.source is not None else None,
            attempts=delivery.envelope.attempts,
            duration_s=duration_s,
            failure=failure,
            meta=copy.deepcopy(delivery.envelope.meta),
        )
        await self.app.emit_event(event)
