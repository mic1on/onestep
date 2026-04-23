from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from .envelope import Envelope
from .state import ScopedState

if TYPE_CHECKING:
    from .app import OneStepApp
    from .connectors.base import Delivery, Sink
    from .task import TaskSpec


class TaskContext:
    def __init__(self, *, app: "OneStepApp", task: "TaskSpec", delivery: "Delivery") -> None:
        self.app = app
        self.task = task
        self.delivery = delivery
        self.logger = logging.getLogger(f"onestep.{app.name}.{task.name}")
        self.config = app.config
        self.state = ScopedState(app.state, f"{app.name}:{task.name}")

    @property
    def current(self) -> Envelope:
        return self.delivery.envelope

    async def update_current_row(self, values: Mapping[str, Any]) -> None:
        if not isinstance(values, Mapping):
            raise TypeError("update_current_row() requires a mapping payload")
        updater = getattr(self.delivery, "update_current_row", None)
        if updater is None:
            raise RuntimeError(
                "update_current_row() is only supported for deliveries that can mutate their current row"
            )
        await updater(dict(values))

    async def emit(
        self,
        body: Any,
        *,
        sink: "Sink | None" = None,
        meta: Mapping[str, Any] | None = None,
    ) -> None:
        sinks = (sink,) if sink is not None else self.task.sinks
        if not sinks:
            raise RuntimeError("emit() requires at least one sink")
        envelope = Envelope(body=body, meta=dict(meta or {}))
        for target in sinks:
            await target.send(envelope)
