"""Task-level operations that need no runner registry.

This module hosts the two operations that are invoked from the control plane
without needing the per-task :class:`asyncio.Task` handles: dead-letter replay,
dead-letter discard, and one-shot manual run. The capability probes that the
control plane uses to decide which commands are supported live here too.

Public behaviour is unchanged; the methods are invoked through facade
delegation on :class:`OneStepApp`.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from ..connectors.base import Sink, Source
from ..envelope import Envelope
from ..runtime.runner import TaskRunner
from ..task import TaskSpec


class _SyntheticManualRunDelivery:
    def __init__(self, envelope: Envelope) -> None:
        self.envelope = envelope
        self.acked = False
        self.failed = False
        self.retry_requested = False
        self.retry_delay_s: float | None = None

    @property
    def payload(self) -> Any:
        return self.envelope.body

    async def start_processing(self) -> None:
        return None

    async def ack(self) -> None:
        self.acked = True

    async def retry(self, *, delay_s: float | None = None) -> None:
        self.retry_requested = True
        self.retry_delay_s = delay_s
        self.envelope = Envelope(
            body=copy.deepcopy(self.envelope.body),
            meta=copy.deepcopy(self.envelope.meta),
            attempts=self.envelope.attempts + 1,
        )

    async def fail(self, exc: Exception | None = None) -> None:
        self.failed = True


class TaskOperations:
    """Dead-letter replay/discard and one-shot manual run for a :class:`OneStepApp`.

    The component receives the owning app so it can read the task registry and
    the events logger without exposing the underlying lists. It does not need
    access to the runner registry or asyncio state — that stays on
    :class:`LifecycleController`.
    """

    def __init__(self, app: Any) -> None:
        self._app = app

    @property
    def events_logger(self) -> Any:
        return self._app.events_logger

    # ----- capability probes (control-plane reads) -------------------------

    def supports_dead_letter_replay_commands(self) -> bool:
        return any(self._task_supports_dead_letter_replay(task) for task in self._app._tasks)

    def supports_dead_letter_discard_commands(self) -> bool:
        return any(self._task_supports_dead_letter_discard(task) for task in self._app._tasks)

    def supports_manual_run_commands(self) -> bool:
        return any(self._task_supports_manual_run(task) for task in self._app._tasks)

    # ----- public operations ------------------------------------------------

    async def replay_task_dead_letters(self, task_name: str, *, limit: int) -> dict[str, Any]:
        if limit < 1:
            raise ValueError("dead-letter replay limit must be >= 1")
        task = self._require_dead_letter_replay_task(task_name)
        assert task.source is not None
        assert isinstance(task.source, Sink)
        dead_letter_source = task.dead_letter_sinks[0]
        assert isinstance(dead_letter_source, Source)

        attempted_count = 0
        replayed_count = 0
        failed_count = 0

        for delivery in await dead_letter_source.fetch(limit):
            attempted_count += 1
            try:
                replay_envelope = self._build_dead_letter_replay_envelope(delivery.envelope)
            except Exception:
                failed_count += 1
                self.events_logger.exception(
                    "dead-letter replay payload was invalid",
                    extra={"task_name": task_name},
                )
                try:
                    await delivery.retry()
                except Exception:
                    pass
                continue

            try:
                await task.source.send(replay_envelope)
            except Exception:
                failed_count += 1
                self.events_logger.exception(
                    "dead-letter replay publish failed",
                    extra={"task_name": task_name},
                )
                try:
                    await delivery.retry()
                except Exception:
                    pass
                continue

            try:
                await delivery.ack()
            except Exception:
                failed_count += 1
                self.events_logger.exception(
                    "dead-letter replay ack failed after publish",
                    extra={"task_name": task_name},
                )
                continue

            replayed_count += 1

        if attempted_count == 0:
            completion = "complete"
        elif failed_count == 0:
            completion = "complete"
        elif replayed_count > 0:
            completion = "partial"
        else:
            completion = "failed"

        return {
            "operation": "replay_dead_letters",
            "task_name": task_name,
            "requested": True,
            "completion": completion,
            "requested_limit": limit,
            "attempted_count": attempted_count,
            "replayed_count": replayed_count,
            "failed_count": failed_count,
            "empty": attempted_count == 0,
        }

    async def discard_task_dead_letters(self, task_name: str, *, limit: int) -> dict[str, Any]:
        if limit < 1:
            raise ValueError("dead-letter discard limit must be >= 1")
        task = self._require_dead_letter_discard_task(task_name)
        dead_letter_source = task.dead_letter_sinks[0]
        assert isinstance(dead_letter_source, Source)

        attempted_count = 0
        discarded_count = 0
        failed_count = 0

        for delivery in await dead_letter_source.fetch(limit):
            attempted_count += 1
            try:
                await delivery.ack()
            except Exception:
                failed_count += 1
                self.events_logger.exception(
                    "dead-letter discard ack failed",
                    extra={"task_name": task_name},
                )
                continue
            discarded_count += 1

        if attempted_count == 0:
            completion = "complete"
        elif failed_count == 0:
            completion = "complete"
        elif discarded_count > 0:
            completion = "partial"
        else:
            completion = "failed"

        return {
            "operation": "discard_dead_letters",
            "task_name": task_name,
            "requested": True,
            "completion": completion,
            "requested_limit": limit,
            "attempted_count": attempted_count,
            "discarded_count": discarded_count,
            "failed_count": failed_count,
            "empty": attempted_count == 0,
        }

    async def run_task_once(
        self,
        task_name: str,
        *,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        task = self._require_manual_run_task(task_name)
        delivery = _SyntheticManualRunDelivery(
            Envelope(
                body=copy.deepcopy(dict(payload)),
                meta={
                    "manual_run": True,
                    "task_name": task_name,
                },
                attempts=0,
            )
        )
        runner = TaskRunner(self._app, task)

        attempted_count = 0
        completed = False
        last_failure: Exception | None = None

        while True:
            attempted_count += 1
            await runner._handle_delivery(delivery)
            if delivery.acked:
                completed = True
                break
            if not delivery.retry_requested:
                break
            delivery.retry_requested = False
            last_failure = RuntimeError("manual run exhausted retries")

        if completed:
            return {
                "operation": "run_task_once",
                "task_name": task_name,
                "requested": True,
                "completion": "complete",
                "attempted_count": attempted_count,
                "manual_run": True,
            }

        if delivery.failed:
            raise RuntimeError(
                f"manual run failed for task {task_name} after {attempted_count} attempt(s)"
            ) from last_failure

        raise RuntimeError(
            f"manual run did not reach a terminal successful state for task {task_name}"
        )

    # ----- internals --------------------------------------------------------

    def _require_dead_letter_replay_task(self, task_name: str) -> TaskSpec:
        task = self._require_controllable_task(task_name)
        if not self._task_supports_dead_letter_replay(task):
            raise ValueError(
                f"task {task_name} does not support dead-letter replay with the configured source and dead-letter connectors"
            )
        return task

    def _require_dead_letter_discard_task(self, task_name: str) -> TaskSpec:
        task = self._require_controllable_task(task_name)
        if not self._task_supports_dead_letter_discard(task):
            raise ValueError(
                f"task {task_name} does not support dead-letter discard with the configured dead-letter connectors"
            )
        return task

    def _require_manual_run_task(self, task_name: str) -> TaskSpec:
        task = self._require_controllable_task(task_name)
        if not self._task_supports_manual_run(task):
            raise ValueError(
                f"task {task_name} does not support manual run with the configured source"
            )
        return task

    def _require_controllable_task(self, task_name: str) -> TaskSpec:
        task = next((task for task in self._app._tasks if task.name == task_name), None)
        if task is None:
            raise ValueError(f"task {task_name} was not found")
        if task.source is None:
            raise ValueError(f"task {task_name} does not have a controllable source runner")
        return task

    def _task_supports_dead_letter_discard(self, task: TaskSpec) -> bool:
        return len(task.dead_letter_sinks) == 1 and isinstance(task.dead_letter_sinks[0], Source)

    def _task_supports_dead_letter_replay(self, task: TaskSpec) -> bool:
        return (
            task.source is not None
            and isinstance(task.source, Sink)
            and self._task_supports_dead_letter_discard(task)
        )

    def _task_supports_manual_run(self, task: TaskSpec) -> bool:
        return task.source is not None and bool(getattr(task.source, "supports_manual_run", False))

    def _build_dead_letter_replay_envelope(self, dead_letter_envelope: Envelope) -> Envelope:
        if not isinstance(dead_letter_envelope.body, Mapping):
            raise ValueError("dead-letter envelope body must be a mapping")
        if "payload" not in dead_letter_envelope.body:
            raise ValueError("dead-letter envelope body is missing payload")

        original_payload = copy.deepcopy(dead_letter_envelope.body["payload"])
        original_meta = dead_letter_envelope.meta.get("original_meta")
        if not isinstance(original_meta, Mapping):
            replay_meta: dict[str, Any] = {}
        else:
            replay_meta = copy.deepcopy(dict(original_meta))

        original_attempts = dead_letter_envelope.meta.get("original_attempts")
        replay_attempts = original_attempts if isinstance(original_attempts, int) and original_attempts >= 0 else 0
        return Envelope(
            body=original_payload,
            meta=replay_meta,
            attempts=replay_attempts,
        )
