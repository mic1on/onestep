from __future__ import annotations

import math
import multiprocessing
import os
import sys
import tempfile
import time
from dataclasses import replace
from multiprocessing.connection import Connection
from multiprocessing.context import BaseContext
from typing import Any

from .ipc import FrameValidator, IPCProtocolError, decode_frame, encode_frame
from .models import SIDE_EFFECT_WARNING, DiagnosticReport, DiagnosticRequest, encode_request
from .worker import diagnostic_worker_main

_PARTIAL_SEND_WARNING = (
    f"{SIDE_EFFECT_WARNING}; forced termination during --send may leave a partial "
    "external write and a retry may create a duplicate"
)


def supervise_diagnostic(
    request: DiagnosticRequest,
    *,
    timeout_s: float = 60.0,
    grace_s: float = 5.0,
) -> DiagnosticReport:
    if (
        isinstance(timeout_s, bool)
        or not isinstance(timeout_s, (int, float))
        or timeout_s <= 0
        or not math.isfinite(float(timeout_s))
        or isinstance(grace_s, bool)
        or not isinstance(grace_s, (int, float))
        or grace_s < 0
        or not math.isfinite(float(grace_s))
    ):
        raise ValueError("diagnostic timeout must be > 0 and grace must be >= 0")
    return _SpawnSupervisor(
        request,
        timeout_s=float(timeout_s),
        grace_s=float(grace_s),
    ).run()


class _SpawnSupervisor:
    def __init__(
        self,
        request: DiagnosticRequest,
        *,
        timeout_s: float,
        grace_s: float,
    ) -> None:
        self.request = request
        self.timeout_s = timeout_s
        self.grace_s = grace_s
        self.validator = FrameValidator(direction="status")
        self.last_checkpoint: dict[str, Any] | None = None
        self.send_incomplete = False
        self.any_send_completed = False
        self.selected_sinks: tuple[str, ...] = ()

    def run(self) -> DiagnosticReport:
        ctx = multiprocessing.get_context("spawn")
        control_rx, control_tx = ctx.Pipe(duplex=False)
        status_rx, status_tx = ctx.Pipe(duplex=False)
        process = None
        started = False
        child_log = tempfile.TemporaryFile(mode="w+b")
        stderr_handle = _duplicate_stderr_handle(ctx, child_log.fileno())
        started_at = time.monotonic()
        try:
            process = ctx.Process(
                target=diagnostic_worker_main,
                args=(
                    encode_request(self.request),
                    control_rx,
                    status_tx,
                    stderr_handle,
                ),
                name="onestep-diagnostic",
            )
            process.start()
            started = True
            deadline = time.monotonic() + self.timeout_s
            control_rx.close()
            status_tx.close()

            report = self._wait_for_final(
                status_rx,
                process,
                deadline=deadline,
            )
            if report is not None:
                return self._with_last_checkpoint(report)

            try:
                control_tx.send_bytes(
                    encode_frame("cancel", sequence=1, payload={})
                )
            except (BrokenPipeError, EOFError, OSError):
                pass
            grace_deadline = time.monotonic() + self.grace_s
            report = self._wait_for_final(
                status_rx,
                process,
                deadline=grace_deadline,
                child_exit_is_failure=False,
            )
            if report is not None:
                report = self._with_last_checkpoint(report)
                return replace(
                    report,
                    completion="timed_out",
                    duration_s=time.monotonic() - started_at,
                    warning=(
                        _PARTIAL_SEND_WARNING
                        if self.send_incomplete
                        else report.warning
                    ),
                    side_effect_outcome=(
                        "unknown"
                        if self.send_incomplete
                        else report.side_effect_outcome
                    ),
                )
            return self._synthesize(
                completion="timed_out",
                duration_s=time.monotonic() - started_at,
            )
        except IPCProtocolError:
            return self._synthesize(
                completion="child_failed",
                duration_s=time.monotonic() - started_at,
            )
        except (EOFError, BrokenPipeError, OSError):
            return self._synthesize(
                completion="child_failed",
                duration_s=time.monotonic() - started_at,
            )
        finally:
            for endpoint in (control_tx, control_rx, status_tx, status_rx):
                try:
                    endpoint.close()
                except OSError:
                    pass
            if started and process is not None:
                if process.is_alive():
                    process.terminate()
                process.join(timeout=self.grace_s)
                if process.is_alive() and hasattr(process, "kill"):
                    process.kill()
                    process.join()
            _forward_child_log(child_log)

    def _wait_for_final(
        self,
        status_rx: Connection,
        process: Any,
        *,
        deadline: float,
        child_exit_is_failure: bool = True,
    ) -> DiagnosticReport | None:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            if status_rx.poll(min(remaining, 0.05)):
                frame = decode_frame(status_rx.recv_bytes())
                payload = self.validator.accept(frame)
                if frame["kind"] == "checkpoint":
                    self._retain_checkpoint(payload)
                    continue
                return DiagnosticReport.from_dict(payload)
            if not process.is_alive():
                if status_rx.poll(0):
                    continue
                if child_exit_is_failure:
                    raise EOFError("diagnostic child exited without a final frame")
                return None

    def _retain_checkpoint(self, checkpoint: dict[str, Any]) -> None:
        self.last_checkpoint = dict(checkpoint)
        selected = checkpoint.get("selected_sinks")
        if isinstance(selected, list):
            self.selected_sinks = tuple(selected)
        if checkpoint["phase"] != "sink":
            return
        if checkpoint["transition"] == "entered":
            self.send_incomplete = True
        else:
            self.send_incomplete = False
            self.any_send_completed = True

    def _with_last_checkpoint(self, report: DiagnosticReport) -> DiagnosticReport:
        if report.last_checkpoint is not None or self.last_checkpoint is None:
            return report
        return replace(report, last_checkpoint=dict(self.last_checkpoint))

    def _synthesize(self, *, completion: str, duration_s: float) -> DiagnosticReport:
        checkpoint = self.last_checkpoint or {
            "phase": "child_start",
            "transition": "entered",
            "elapsed_s": 0.0,
        }
        if self.send_incomplete:
            side_effect_outcome = "unknown"
        elif self.any_send_completed:
            side_effect_outcome = "completed"
        else:
            side_effect_outcome = "not_attempted"
        return DiagnosticReport(
            operation=self.request.operation,
            app=str(checkpoint.get("app", "")),
            task=self.request.task,
            mode="send" if self.request.send else "dry-run",
            completion=completion,
            attempts=(
                self.request.envelope.attempts
                if self.request.envelope is not None
                else 0
            ),
            selected_sinks=self.selected_sinks,
            delivery_action=None,
            delivery_action_basis="predicted",
            dead_letter={"attempted": False, "published": None},
            events=(),
            duration_s=duration_s,
            warning=(
                _PARTIAL_SEND_WARNING
                if self.send_incomplete
                else SIDE_EFFECT_WARNING
            ),
            failure=None,
            failure_stage=str(checkpoint.get("phase", "child_start")),
            cleanup="incomplete",
            side_effect_outcome=side_effect_outcome,
            last_checkpoint=dict(checkpoint),
        )


def _duplicate_stderr_handle(ctx: BaseContext, fd: int) -> Any:
    if os.name != "nt":
        from multiprocessing.reduction import DupFd

        return DupFd(fd)
    import msvcrt
    from multiprocessing.reduction import DupHandle

    return DupHandle(msvcrt.get_osfhandle(fd))


def _forward_child_log(handle: Any) -> None:
    try:
        handle.seek(0)
        data = handle.read()
    finally:
        handle.close()
    if not data:
        return
    stream = getattr(sys.stderr, "buffer", sys.stderr)
    if stream is sys.stderr:
        stream.write(data.decode("utf-8", errors="replace"))
    else:
        stream.write(data)
    sys.stderr.flush()


__all__ = ["supervise_diagnostic"]
