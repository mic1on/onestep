from __future__ import annotations

import copy
import time
from typing import Any

from onestep.app import OneStepApp
from onestep.capture.codec import encode_value
from onestep.connectors.base import Delivery, Sink
from onestep.envelope import Envelope
from onestep.events import TaskEvent
from onestep.runtime.executor import (
    Checkpoint,
    DeliveryAction,
    DeliveryExecutor,
    ExecutionOutcome,
)

from .models import DiagnosticReport


async def _noop_checkpoint(phase, transition, details) -> None:
    return None


class _DiagnosticDelivery(Delivery):
    async def ack(self) -> None:
        return None

    async def retry(self, *, delay_s: float | None = None) -> None:
        return None

    async def fail(self, exc: Exception | None = None) -> None:
        return None


class DiagnosticRunner:
    def __init__(
        self,
        app: OneStepApp,
        *,
        checkpoint: Checkpoint | None = None,
    ) -> None:
        self.app = app
        self.checkpoint = checkpoint or _noop_checkpoint
        self._send = False
        self._opened: list[Sink] = []
        self._opened_ids: set[int] = set()
        self._outputs: list[dict[str, Any]] = []
        self._real_sends = 0

    async def run(
        self,
        *,
        task_name: str,
        envelope: Envelope,
        send: bool,
        operation: str = "run",
    ) -> DiagnosticReport:
        task = self._resolve_task(task_name)
        events: list[TaskEvent] = []
        delivery = _DiagnosticDelivery(envelope)
        self._send = send
        self._opened = []
        self._opened_ids = set()
        self._outputs = []
        self._real_sends = 0

        async def emit_event(event: TaskEvent) -> None:
            events.append(event)

        executor = DeliveryExecutor(
            self.app,
            task,
            emit_event=emit_event,
            dispatch_sink=self._dispatch_sink,
            apply_delivery_actions=False,
            checkpoint=self.checkpoint,
        )
        started_at = time.perf_counter()
        cleanup_errors: list[Exception] = []
        outcome: ExecutionOutcome | None = None
        try:
            outcome = await executor.execute(delivery)
        finally:
            for sink in reversed(self._opened):
                sink_name = getattr(sink, "name", type(sink).__name__)
                try:
                    await self.checkpoint(
                        "cleanup",
                        "entered",
                        {"resource": sink_name},
                    )
                except Exception as exc:
                    cleanup_errors.append(exc)
                try:
                    await sink.close()
                except Exception as exc:
                    cleanup_errors.append(exc)
                try:
                    await self.checkpoint(
                        "cleanup",
                        "completed",
                        {
                            "resource": sink_name,
                            "cleanup": (
                                "failed" if cleanup_errors else "complete"
                            ),
                        },
                    )
                except Exception as exc:
                    cleanup_errors.append(exc)
        assert outcome is not None
        return self._build_report(
            operation=operation,
            task_name=task_name,
            send=send,
            envelope=envelope,
            events=events,
            outcome=outcome,
            cleanup_errors=cleanup_errors,
            duration_s=time.perf_counter() - started_at,
        )

    def _resolve_task(self, task_name: str):
        matches = [task for task in self.app.tasks if task.name == task_name]
        if len(matches) != 1:
            raise ValueError(
                f"expected exactly one task named {task_name!r}, found {len(matches)}"
            )
        return matches[0]

    async def _dispatch_sink(
        self,
        sink: Sink,
        envelope: Envelope,
        kind: str,
    ) -> bool:
        sink_name = getattr(sink, "name", type(sink).__name__)
        output = {
            "sink": sink_name,
            "kind": kind,
            "envelope": {
                "body": encode_value(self._report_body(envelope.body, kind=kind)),
                "meta": encode_value(envelope.meta),
                "attempts": envelope.attempts,
            },
        }
        self._outputs.append(output)
        if not self._send:
            return False
        if id(sink) not in self._opened_ids:
            self._opened_ids.add(id(sink))
            self._opened.append(sink)
            await self.checkpoint("sink_open", "entered", {"resource": sink_name})
            await sink.open()
            await self.checkpoint("sink_open", "completed", {"resource": sink_name})
        await self.checkpoint("sink", "entered", {"resource": sink_name})
        await sink.send(envelope)
        self._real_sends += 1
        await self.checkpoint("sink", "completed", {"resource": sink_name})
        return True

    @staticmethod
    def _report_body(body: Any, *, kind: str) -> Any:
        if kind != "dead_letter" or not isinstance(body, dict):
            return body
        sanitized = copy.deepcopy(body)
        failure = sanitized.get("failure")
        if isinstance(failure, dict):
            failure.pop("message", None)
            failure.pop("traceback", None)
        return sanitized

    def _build_report(
        self,
        *,
        operation: str,
        task_name: str,
        send: bool,
        envelope: Envelope,
        events: list[TaskEvent],
        outcome: ExecutionOutcome,
        cleanup_errors: list[Exception],
        duration_s: float,
    ) -> DiagnosticReport:
        action_map = {
            DeliveryAction.ACK: "would_ack",
            DeliveryAction.RETRY: "would_retry",
            DeliveryAction.DEAD_LETTER: "would_dead_letter",
            DeliveryAction.FAIL: "would_fail",
            None: None,
        }
        completion = outcome.completion
        if cleanup_errors:
            completion = "failed"
        return DiagnosticReport(
            operation=operation,
            app=self.app.name,
            task=task_name,
            mode="send" if send else "dry-run",
            completion=completion,
            attempts=envelope.attempts,
            selected_sinks=tuple(outcome.selected_sinks),
            delivery_action=action_map[outcome.delivery_action],
            delivery_action_basis="predicted",
            dead_letter={
                "attempted": outcome.dead_letter_attempted,
                "published": outcome.dead_letter_published,
            },
            events=tuple(events),
            duration_s=duration_s,
            outputs=tuple(self._outputs),
            failure=outcome.public_failure,
            failure_stage=outcome.failure_stage,
            cleanup="failed" if cleanup_errors else "complete",
            side_effect_outcome=(
                "completed"
                if self._real_sends
                else "not_attempted"
            ),
        )
__all__ = ["DiagnosticRunner"]
