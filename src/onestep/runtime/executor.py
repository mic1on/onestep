from __future__ import annotations

import asyncio
import copy
import inspect
import logging
import math
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

from onestep.capture.codec import CaptureEncodingError
from onestep.connectors.base import Delivery, Sink
from onestep.context import TaskContext
from onestep.envelope import Envelope
from onestep.events import TaskEvent, TaskEventKind
from onestep.execution import (
    ExecutionCompletion,
    ExecutionErrorDetail,
    ExecutionLeaseLost,
    ExecutionStatus,
    ManagedExecutionDelivery,
)
from onestep.invoke import invoke_callback
from onestep.resilience import (
    ConnectorOperationError,
    connector_retry_delay,
    is_retryable_connector_error,
)
from onestep.retry import FailureInfo, FailureKind, RetryDecision, resolve_retry_action
from onestep.task import EmitBinding, EmitRoute, TaskSpec

if TYPE_CHECKING:
    from onestep.app import OneStepApp


_INVALID_NOTIFICATION_VALUE = object()


class DeliveryAction(str, Enum):
    ACK = "ack"
    RETRY = "retry"
    DEAD_LETTER = "dead_letter"
    FAIL = "fail"


@dataclass
class ExecutionOutcome:
    completion: str
    handler_result: Any = None
    selected_sinks: list[str] = field(default_factory=list)
    delivery_action: DeliveryAction | None = None
    retry_delay_s: float | None = None
    failure: FailureInfo | None = None
    public_failure: dict[str, str] | None = None
    failure_stage: str | None = None
    dead_letter_attempted: bool = False
    dead_letter_published: bool | None = None
    terminal: bool = False
    capture_envelope: Envelope | None = None
    capture_snapshot_error_type: str | None = None


EventEmitter = Callable[[TaskEvent], Awaitable[None]]
SinkDispatcher = Callable[[Sink, Envelope, str], Awaitable[bool]]
Checkpoint = Callable[[str, str, Mapping[str, Any]], Awaitable[None]]


async def _noop_checkpoint(
    phase: str,
    transition: str,
    details: Mapping[str, Any],
) -> None:
    return None


class DeliveryExecutor:
    _SEND_ATTEMPTS = 2

    def __init__(
        self,
        app: "OneStepApp",
        task: TaskSpec,
        *,
        emit_event: EventEmitter | None = None,
        dispatch_sink: SinkDispatcher | None = None,
        apply_delivery_actions: bool = True,
        checkpoint: Checkpoint | None = None,
    ) -> None:
        self.app = app
        self.task = task
        self._event_emitter = emit_event or app.emit_event
        self._sink_dispatcher = dispatch_sink or self._dispatch_production_sink
        self.apply_delivery_actions = apply_delivery_actions
        self.checkpoint = checkpoint or _noop_checkpoint
        self.logger = logging.getLogger(f"onestep.{app.name}.{task.name}")

    async def execute(self, delivery: Delivery) -> ExecutionOutcome:
        outcome = ExecutionOutcome(completion="running")
        ctx = TaskContext(app=self.app, task=self.task, delivery=delivery)
        started_at = time.perf_counter()
        active_stage = "delivery_action"
        try:
            await delivery.start_processing()
            await self.emit(TaskEventKind.STARTED, delivery)

            active_stage = "before_hook"
            await self.checkpoint(active_stage, "entered", {})
            await self._run_hooks(self.task.hooks.before, ctx, delivery.payload)
            await self.checkpoint(active_stage, "completed", {})

            active_stage = "handler"
            await self.checkpoint(active_stage, "entered", {})
            outcome.handler_result = await self._invoke_handler(ctx, delivery)
            await self.checkpoint(active_stage, "completed", {})

            active_stage = "after_success_hook"
            await self.checkpoint(active_stage, "entered", {})
            await self._run_hooks(
                self.task.hooks.after_success,
                ctx,
                delivery.payload,
                outcome.handler_result,
            )
            await self.checkpoint(active_stage, "completed", {})

            if outcome.handler_result is not None and self.task.emit_targets:
                active_stage = "route"
                await self.checkpoint(active_stage, "entered", {})
                selected = await self._select_emit_bindings(
                    ctx,
                    delivery.payload,
                    outcome.handler_result,
                )
                outcome.selected_sinks = [
                    getattr(binding.sink, "name", type(binding.sink).__name__) for binding in selected
                ]
                await self.checkpoint(
                    active_stage,
                    "completed",
                    {"selected_sinks": outcome.selected_sinks},
                )
                active_stage = "transform"
                await self.checkpoint(active_stage, "entered", {})
                prepared = await self._prepare_emit_envelopes(
                    selected,
                    ctx,
                    delivery.payload,
                    outcome.handler_result,
                )
                await self.checkpoint(active_stage, "completed", {})
                for sink, emitted in prepared:
                    active_stage = "sink"
                    await self._sink_dispatcher(sink, emitted, "emit")

            active_stage = "ack"
            outcome.delivery_action = DeliveryAction.ACK
            await self.checkpoint(active_stage, "entered", {})
            if self.apply_delivery_actions:
                await self._apply_success(delivery, outcome.handler_result)
            await self.checkpoint(active_stage, "completed", {})
            await self.emit(
                TaskEventKind.SUCCEEDED,
                delivery,
                duration_s=time.perf_counter() - started_at,
                event_meta=self._build_succeeded_event_meta(
                    delivery,
                    outcome.handler_result,
                ),
            )
            outcome.completion = "succeeded"
            outcome.terminal = True
            return outcome
        except asyncio.CancelledError:
            outcome.failure_stage = active_stage
            await self._handle_cancelled(delivery, outcome, started_at)
            raise
        except asyncio.TimeoutError as exc:
            outcome.failure_stage = active_stage
            return await self._handle_failure(
                ctx,
                delivery,
                exc,
                FailureKind.TIMEOUT,
                outcome,
                started_at,
            )
        except Exception as exc:
            outcome.failure_stage = active_stage
            return await self._handle_failure(
                ctx,
                delivery,
                exc,
                FailureKind.ERROR,
                outcome,
                started_at,
            )

    async def emit(
        self,
        kind: TaskEventKind,
        delivery: Delivery,
        *,
        duration_s: float | None = None,
        failure: FailureInfo | None = None,
        event_meta: dict[str, Any] | None = None,
    ) -> None:
        event = TaskEvent(
            kind=kind,
            app=self.app.name,
            task=self.task.name,
            source=self.task.source.name if self.task.source is not None else None,
            attempts=delivery.envelope.attempts,
            duration_s=duration_s,
            failure=failure,
            meta=(
                copy.deepcopy(event_meta)
                if event_meta is not None
                else copy.deepcopy(delivery.envelope.meta)
            ),
        )
        await self._event_emitter(event)

    async def _invoke_handler(self, ctx: TaskContext, delivery: Delivery) -> Any:
        result = self.task.handler(ctx, delivery.payload)
        if inspect.isawaitable(result):
            if self.task.timeout_s is not None:
                return await asyncio.wait_for(result, timeout=self.task.timeout_s)
            return await result
        return result

    async def _select_emit_bindings(
        self,
        ctx: TaskContext,
        payload: Any,
        result: Any,
    ) -> tuple[EmitBinding, ...]:
        bindings: list[EmitBinding] = []
        for target in self.task.emit_targets:
            if isinstance(target, EmitBinding):
                bindings.append(target)
                continue
            assert isinstance(target, EmitRoute)
            if target.predicate is None:
                bindings.extend(target.then_bindings)
                continue
            predicate_result = invoke_callback(target.predicate, ctx, payload, result)
            if inspect.isawaitable(predicate_result):
                predicate_result = await predicate_result
            if predicate_result:
                bindings.extend(target.then_bindings)
            else:
                bindings.extend(target.otherwise_bindings)
        return tuple(bindings)

    async def _prepare_emit_envelopes(
        self,
        bindings: tuple[EmitBinding, ...],
        ctx: TaskContext,
        payload: Any,
        result: Any,
    ) -> tuple[tuple[Sink, Envelope], ...]:
        prepared: list[tuple[Sink, Envelope]] = []
        for binding in bindings:
            body = result
            if binding.transform is not None:
                transformed = invoke_callback(binding.transform, ctx, payload, result)
                body = await transformed if inspect.isawaitable(transformed) else transformed
            prepared.append((binding.sink, Envelope(body=body)))
        return tuple(prepared)

    async def _handle_cancelled(
        self,
        delivery: Delivery,
        outcome: ExecutionOutcome,
        started_at: float,
    ) -> None:
        failure = FailureInfo.from_exception(
            asyncio.CancelledError(),
            kind=FailureKind.CANCELLED,
        )
        outcome.failure = failure
        self._snapshot_capture_envelope(delivery, outcome)
        outcome.public_failure = self._public_failure(failure, None)
        outcome.delivery_action = DeliveryAction.RETRY
        managed = self._managed_delivery(delivery)
        managed_status = (
            ExecutionStatus.CANCELLED
            if managed is not None and managed.cancel_requested
            else ExecutionStatus.RETRYING
        )
        outcome.completion = (
            "cancelled" if managed is None else managed_status.value
        )
        outcome.terminal = (
            managed is not None and managed_status is ExecutionStatus.CANCELLED
        )
        self.logger.warning(
            "task cancelled",
            extra={"failure_kind": failure.kind.value},
        )
        duration_s = time.perf_counter() - started_at
        await self.emit(
            TaskEventKind.CANCELLED,
            delivery,
            failure=failure,
            duration_s=duration_s,
        )
        if self.apply_delivery_actions:
            error = self._execution_error(outcome)
            if managed is None:
                await delivery.retry()
            else:
                await self._complete_managed_execution(
                    managed,
                    ExecutionCompletion(
                        status=managed_status,
                        error=error,
                        delay_s=0 if managed_status is ExecutionStatus.RETRYING else None,
                    )
                )
        if managed is None or managed_status is ExecutionStatus.RETRYING:
            await self.emit(
                TaskEventKind.RETRIED,
                delivery,
                failure=failure,
                duration_s=duration_s,
            )
        await self._capture_failure(delivery, outcome)

    async def _handle_failure(
        self,
        ctx: TaskContext,
        delivery: Delivery,
        exc: Exception,
        kind: FailureKind,
        outcome: ExecutionOutcome,
        started_at: float,
    ) -> ExecutionOutcome:
        duration_s = time.perf_counter() - started_at
        failure = FailureInfo.from_exception(exc, kind=kind)
        outcome.failure = failure
        self._snapshot_capture_envelope(delivery, outcome)
        outcome.public_failure = self._public_failure(failure, exc)
        outcome.completion = "failed"
        ctx.logger.exception(
            "task failed",
            extra={"failure_kind": failure.kind.value},
        )
        await self.checkpoint("failure_hook", "entered", {})
        await self._run_hooks(
            self.task.hooks.on_failure,
            ctx,
            delivery.payload,
            failure,
            suppress_exceptions=True,
            logger=ctx.logger,
            message="task failure hook failed",
        )
        await self.checkpoint("failure_hook", "completed", {})

        action = resolve_retry_action(self.task.retry, delivery.envelope, exc, failure)
        if action.decision is RetryDecision.RETRY:
            outcome.delivery_action = DeliveryAction.RETRY
            outcome.retry_delay_s = action.delay_s
            await self._apply_retry(
                delivery,
                delay_s=action.delay_s,
                error=self._execution_error(outcome),
            )
            await self.emit(
                TaskEventKind.RETRIED,
                delivery,
                failure=failure,
                duration_s=duration_s,
            )
            await self._capture_failure(delivery, outcome)
            return outcome

        if self.task.dead_letter_sinks:
            outcome.delivery_action = DeliveryAction.DEAD_LETTER
            published = await self._publish_dead_letter(
                ctx,
                delivery,
                failure,
                outcome,
                duration_s=duration_s,
            )
            if not published:
                await self._capture_failure(delivery, outcome)
                return outcome
        else:
            outcome.delivery_action = DeliveryAction.FAIL

        outcome.terminal = await self._fail_delivery(
            ctx,
            delivery,
            exc,
            self._execution_error(outcome),
        )
        if not outcome.terminal:
            outcome.delivery_action = DeliveryAction.RETRY
        await self.emit(
            TaskEventKind.FAILED,
            delivery,
            failure=failure,
            duration_s=duration_s,
        )
        await self._capture_failure(delivery, outcome)
        return outcome

    async def _publish_dead_letter(
        self,
        ctx: TaskContext,
        delivery: Delivery,
        failure: FailureInfo,
        outcome: ExecutionOutcome,
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
                "source": (
                    self.task.source.name if self.task.source is not None else None
                ),
                "original_meta": copy.deepcopy(delivery.envelope.meta),
                "original_attempts": delivery.envelope.attempts,
            },
            attempts=0,
        )
        any_real_send = False
        try:
            for sink in self.task.dead_letter_sinks:
                sent = await self._sink_dispatcher(sink, envelope, "dead_letter")
                any_real_send = any_real_send or sent
        except Exception:
            outcome.dead_letter_attempted = True
            outcome.dead_letter_published = False
            outcome.delivery_action = DeliveryAction.RETRY
            ctx.logger.exception(
                "dead-letter publish failed; retrying original delivery"
            )
            await self._apply_retry(
                delivery,
                error=self._execution_error(outcome),
            )
            await self.emit(
                TaskEventKind.RETRIED,
                delivery,
                failure=failure,
                duration_s=duration_s,
            )
            return False
        outcome.dead_letter_attempted = any_real_send
        outcome.dead_letter_published = True if any_real_send else None
        await self.emit(
            TaskEventKind.DEAD_LETTERED,
            delivery,
            failure=failure,
            duration_s=duration_s,
        )
        return True

    async def _apply_retry(
        self,
        delivery: Delivery,
        *,
        delay_s: float | None = None,
        error: ExecutionErrorDetail | None = None,
    ) -> None:
        await self.checkpoint("delivery_action", "entered", {})
        if self.apply_delivery_actions:
            managed = self._managed_delivery(delivery)
            if managed is None:
                await delivery.retry(delay_s=delay_s)
            else:
                await self._complete_managed_execution(
                    managed,
                    ExecutionCompletion(
                        status=ExecutionStatus.RETRYING,
                        error=error,
                        delay_s=delay_s,
                    )
                )
        await self.checkpoint("delivery_action", "completed", {})

    async def _fail_delivery(
        self,
        ctx: TaskContext,
        delivery: Delivery,
        exc: Exception,
        error: ExecutionErrorDetail | None = None,
    ) -> bool:
        if not self.apply_delivery_actions:
            return True
        await self.checkpoint("delivery_action", "entered", {})
        managed = self._managed_delivery(delivery)
        if managed is not None:
            try:
                await self._complete_managed_execution(
                    managed,
                    ExecutionCompletion(
                        status=ExecutionStatus.FAILED,
                        error=error,
                    )
                )
            except Exception:
                ctx.logger.exception(
                    "managed execution failure completion failed; retrying original delivery"
                )
                retried = await self._complete_managed_execution(
                    managed,
                    ExecutionCompletion(
                        status=ExecutionStatus.RETRYING,
                        error=error,
                    )
                )
                await self.checkpoint("delivery_action", "completed", {})
                return not retried
            await self.checkpoint("delivery_action", "completed", {})
            return True
        try:
            await delivery.fail(exc)
        except Exception:
            ctx.logger.exception(
                "delivery fail action failed; retrying original delivery"
            )
            await delivery.retry()
            await self.checkpoint("delivery_action", "completed", {})
            return False
        await self.checkpoint("delivery_action", "completed", {})
        return True

    def _managed_delivery(
        self,
        delivery: Delivery,
    ) -> ManagedExecutionDelivery | None:
        if isinstance(delivery, ManagedExecutionDelivery):
            return delivery
        return None

    async def _complete_managed_execution(
        self,
        delivery: ManagedExecutionDelivery,
        completion: ExecutionCompletion,
    ) -> bool:
        try:
            await delivery.complete_execution(completion)
        except ExecutionLeaseLost:
            return False
        return True

    async def _apply_success(self, delivery: Delivery, result: Any) -> None:
        managed = self._managed_delivery(delivery)
        if managed is None:
            await delivery.ack()
            return
        await self._complete_managed_execution(
            managed,
            ExecutionCompletion(
                status=ExecutionStatus.SUCCEEDED,
                result=result,
            )
        )

    def _execution_error(self, outcome: ExecutionOutcome) -> ExecutionErrorDetail | None:
        public_failure = outcome.public_failure
        if public_failure is None:
            return None
        return ExecutionErrorDetail(
            kind=public_failure["failure_kind"],
            exception_type=public_failure["exception_type"],
            stage=outcome.failure_stage,
            backend=public_failure.get("backend"),
            operation=public_failure.get("operation"),
            connector_kind=public_failure.get("connector_kind"),
        )

    async def _dispatch_production_sink(
        self,
        sink: Sink,
        envelope: Envelope,
        kind: str,
    ) -> bool:
        if kind == "dead_letter":
            await sink.send(envelope)
        else:
            await self._send_to_sink(sink, envelope)
        return True

    async def _send_to_sink(self, sink: Sink, envelope: Envelope) -> None:
        fallback_s = getattr(sink, "poll_interval_s", 1.0)
        for attempt in range(self._SEND_ATTEMPTS):
            try:
                await sink.send(envelope)
                self.logger.debug(
                    "sink send succeeded",
                    extra={
                        "sink_name": getattr(
                            sink,
                            "name",
                            sink.__class__.__name__,
                        ),
                        "sink_kind": sink.__class__.__name__,
                        "connector_backend": (
                            getattr(
                                getattr(sink, "connector", None),
                                "__class__",
                                type(None),
                            ).__name__
                            if getattr(sink, "connector", None) is not None
                            else None
                        ),
                        "delivery_attempts": envelope.attempts,
                    },
                )
                return
            except ConnectorOperationError as exc:
                if (
                    not is_retryable_connector_error(exc)
                    or attempt == self._SEND_ATTEMPTS - 1
                ):
                    raise
                delay_s = connector_retry_delay(exc, fallback_s=fallback_s)
                self.logger.warning(
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
                    await asyncio.wait_for(
                        self.app.wait_for_shutdown(),
                        timeout=delay_s,
                    )
                except asyncio.TimeoutError:
                    continue
                raise

    async def _run_hooks(
        self,
        hooks: Sequence[Callable[..., Any]],
        *args: Any,
        suppress_exceptions: bool = False,
        logger: logging.Logger | None = None,
        message: str = "task hook failed",
    ) -> None:
        for hook in hooks:
            try:
                result = invoke_callback(hook, *args)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                if not suppress_exceptions:
                    raise
                active_logger = logger or self.logger
                active_logger.exception(
                    message,
                    extra={
                        "task_hook": getattr(
                            hook,
                            "__name__",
                            hook.__class__.__name__,
                        )
                    },
                )

    def _build_succeeded_event_meta(
        self,
        delivery: Delivery,
        result: Any,
    ) -> dict[str, Any]:
        event_meta = copy.deepcopy(delivery.envelope.meta)
        notification = self._extract_notification_payload(result)
        if notification is not None:
            event_meta["notification"] = notification
        return event_meta

    def _extract_notification_payload(self, result: Any) -> dict[str, Any] | None:
        if not isinstance(result, Mapping):
            return None
        notification = result.get("notification")
        if not isinstance(notification, Mapping):
            return None
        sanitized = self._sanitize_notification_value(notification)
        if not isinstance(sanitized, dict) or not sanitized:
            return None
        return sanitized

    def _sanitize_notification_value(self, value: Any) -> Any:
        if value is None or isinstance(value, (str, bool, int)):
            return value
        if isinstance(value, float):
            return value if math.isfinite(value) else _INVALID_NOTIFICATION_VALUE
        if isinstance(value, Mapping):
            sanitized: dict[str, Any] = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    continue
                clean_item = self._sanitize_notification_value(item)
                if clean_item is _INVALID_NOTIFICATION_VALUE:
                    continue
                sanitized[key] = clean_item
            return sanitized
        if isinstance(value, (list, tuple)):
            sanitized_items = []
            for item in value:
                clean_item = self._sanitize_notification_value(item)
                if clean_item is _INVALID_NOTIFICATION_VALUE:
                    continue
                sanitized_items.append(clean_item)
            return sanitized_items
        return _INVALID_NOTIFICATION_VALUE

    def _public_failure(
        self,
        failure: FailureInfo,
        exc: Exception | None,
    ) -> dict[str, str]:
        result = {
            "failure_kind": failure.kind.value,
            "exception_type": failure.exception_type,
        }
        if isinstance(exc, ConnectorOperationError):
            result.update(
                {
                    "backend": exc.backend,
                    "operation": exc.operation.value,
                    "connector_kind": exc.kind.value,
                }
            )
        return result

    async def _capture_failure(
        self,
        delivery: Delivery,
        outcome: ExecutionOutcome,
    ) -> None:
        writer = getattr(self.app, "_failure_capture_writer", None)
        config = self.app.failure_capture
        if writer is None or config is None or outcome.failure is None:
            return
        if config.mode == "terminal" and not outcome.terminal:
            return
        if outcome.capture_snapshot_error_type is not None:
            self.logger.error(
                "failure capture snapshot failed",
                extra={
                    "app_name": self.app.name,
                    "task_name": self.task.name,
                    "failure_stage": outcome.failure_stage,
                    "capture_type": outcome.capture_snapshot_error_type,
                },
            )
            return
        try:
            await writer.write(
                app=self.app.name,
                task=self.task.name,
                stage=outcome.failure_stage or "delivery_action",
                terminal=outcome.terminal,
                failure=outcome.failure,
                envelope=outcome.capture_envelope or delivery.envelope,
            )
        except CaptureEncodingError as exc:
            self.logger.error(
                "failure capture encoding failed",
                extra={
                    "app_name": self.app.name,
                    "task_name": self.task.name,
                    "failure_stage": outcome.failure_stage,
                    "capture_type": exc.type_name,
                    "capture_path": exc.path,
                },
            )
        except Exception:
            self.logger.exception(
                "failure capture persistence failed",
                extra={
                    "app_name": self.app.name,
                    "task_name": self.task.name,
                },
            )

    def _snapshot_capture_envelope(
        self,
        delivery: Delivery,
        outcome: ExecutionOutcome,
    ) -> None:
        if self.app.failure_capture is None or getattr(
            self.app,
            "_failure_capture_writer",
            None,
        ) is None:
            return
        try:
            outcome.capture_envelope = copy.deepcopy(delivery.envelope)
        except Exception as exc:
            outcome.capture_snapshot_error_type = type(exc).__name__


__all__ = [
    "Checkpoint",
    "DeliveryAction",
    "DeliveryExecutor",
    "ExecutionOutcome",
    "SinkDispatcher",
]
