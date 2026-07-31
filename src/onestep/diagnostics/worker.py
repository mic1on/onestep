from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
import traceback
from dataclasses import replace
from multiprocessing.connection import Connection
from typing import Any

from onestep.capture import load_capture

from .ipc import FrameValidator, decode_frame, encode_frame
from .models import (
    SIDE_EFFECT_WARNING,
    DiagnosticReport,
    decode_request,
)
from .runner import DiagnosticRunner
from .targets import load_diagnostic_target

_PARTIAL_SEND_WARNING = (
    f"{SIDE_EFFECT_WARNING}; forced termination during --send may leave a partial "
    "external write and a retry may create a duplicate"
)


def diagnostic_worker_main(
    request_bytes: bytes,
    control_rx: Connection,
    status_tx: Connection,
    stderr_handle: Any,
) -> None:
    stderr_fd = _detach_stderr_fd(stderr_handle)
    try:
        os.dup2(stderr_fd, 1)
        os.dup2(stderr_fd, 2)
    finally:
        if stderr_fd not in {1, 2}:
            os.close(stderr_fd)
    sys.stdout = os.fdopen(1, "w", buffering=1, closefd=False)
    sys.stderr = os.fdopen(2, "w", buffering=1, closefd=False)
    try:
        asyncio.run(_run_worker(request_bytes, control_rx, status_tx))
    finally:
        control_rx.close()
        status_tx.close()


def _detach_stderr_fd(stderr_handle: Any) -> int:
    detached = stderr_handle.detach()
    if os.name != "nt":
        return detached
    import msvcrt

    return msvcrt.open_osfhandle(detached, os.O_WRONLY)


def _peek_request(data: bytes) -> tuple[str, str, str, bool]:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("malformed diagnostic request") from exc
    if not isinstance(value, dict):
        raise ValueError("diagnostic request must be an object")
    target = value.get("target")
    operation = value.get("operation")
    task = value.get("task")
    send = value.get("send")
    if (
        not isinstance(target, str)
        or operation not in {"run", "replay"}
        or not isinstance(task, str)
        or not isinstance(send, bool)
    ):
        raise ValueError("diagnostic request routing fields are invalid")
    return target, operation, task, send


async def _run_worker(
    request_bytes: bytes,
    control_rx: Connection,
    status_tx: Connection,
) -> None:
    started_at = time.monotonic()
    sequence = 0
    last_checkpoint: dict[str, Any] | None = None
    app_name = ""
    task_name = ""
    operation = "run"
    send = False
    attempts = 0
    send_incomplete = False
    validation_complete = False

    def send_status(kind: str, payload: dict[str, Any]) -> None:
        nonlocal sequence
        sequence += 1
        status_tx.send_bytes(
            encode_frame(kind, sequence=sequence, payload=payload)
        )

    async def checkpoint(
        phase: str,
        transition: str,
        details: Any,
    ) -> None:
        nonlocal last_checkpoint, send_incomplete
        payload: dict[str, Any] = {
            "phase": phase,
            "transition": transition,
            "elapsed_s": time.monotonic() - started_at,
        }
        if app_name:
            payload["app"] = app_name
        if task_name:
            payload["task"] = task_name
        for key in ("resource", "selected_sinks", "completion", "cleanup"):
            value = details.get(key) if hasattr(details, "get") else None
            if value is not None:
                payload[key] = value
        if phase == "sink":
            send_incomplete = transition == "entered"
        last_checkpoint = payload
        send_status("checkpoint", payload)

    await checkpoint("child_start", "entered", {})
    current_task = asyncio.current_task()
    assert current_task is not None
    control_thread = threading.Thread(
        target=_listen_for_cancel,
        args=(control_rx, asyncio.get_running_loop(), current_task),
        daemon=True,
        name="onestep-diagnostic-control",
    )
    control_thread.start()

    try:
        target, operation, task_name, send = _peek_request(request_bytes)
        app = load_diagnostic_target(target)
        app_name = app.name
        request = decode_request(request_bytes)
        await checkpoint("child_start", "completed", {})
        if request.operation == "replay":
            capture = load_capture(
                request.capture_path,
                expected_app=app.name,
                expected_task=request.task,
            )
            envelope = capture.envelope
        else:
            assert request.envelope is not None
            envelope = request.envelope
        attempts = envelope.attempts
        matches = [task for task in app.tasks if task.name == request.task]
        if len(matches) != 1:
            raise ValueError(
                f"expected exactly one task named {request.task!r}, found {len(matches)}"
            )
        validation_complete = True
        report = await DiagnosticRunner(app, checkpoint=checkpoint).run(
            task_name=request.task,
            envelope=envelope,
            send=request.send,
            operation=request.operation,
        )
        report = replace(report, last_checkpoint=last_checkpoint)
    except asyncio.CancelledError:
        report = _fallback_report(
            operation=operation,
            app=app_name,
            task=task_name,
            send=send,
            attempts=attempts,
            completion="cancelled",
            last_checkpoint=last_checkpoint,
            side_effect_unknown=send_incomplete,
            cleanup="complete",
        )
    except BaseException as exc:
        traceback.print_exc(file=sys.stderr)
        report = _fallback_report(
            operation=operation,
            app=app_name,
            task=task_name,
            send=send,
            attempts=attempts,
            completion=(
                "child_failed" if validation_complete else "validation_failed"
            ),
            last_checkpoint=last_checkpoint,
            side_effect_unknown=send_incomplete,
            failure_type=type(exc).__name__,
        )
    try:
        send_status("final", report.to_dict())
    except (BrokenPipeError, EOFError, OSError):
        return


def _listen_for_cancel(
    control_rx: Connection,
    loop: asyncio.AbstractEventLoop,
    task: asyncio.Task[Any],
) -> None:
    validator = FrameValidator(direction="control")
    try:
        while True:
            frame = decode_frame(control_rx.recv_bytes())
            validator.accept(frame)
            if frame["kind"] == "cancel":
                loop.call_soon_threadsafe(task.cancel)
                return
    except (EOFError, OSError, ValueError):
        return


def _fallback_report(
    *,
    operation: str,
    app: str,
    task: str,
    send: bool,
    attempts: int,
    completion: str,
    last_checkpoint: dict[str, Any] | None,
    side_effect_unknown: bool,
    cleanup: str = "incomplete",
    failure_type: str | None = None,
) -> DiagnosticReport:
    checkpoint = last_checkpoint or {
        "phase": "child_start",
        "transition": "entered",
        "elapsed_s": 0.0,
    }
    return DiagnosticReport(
        operation=operation,
        app=app,
        task=task,
        mode="send" if send else "dry-run",
        completion=completion,
        attempts=attempts,
        selected_sinks=(),
        delivery_action=None,
        delivery_action_basis="predicted",
        dead_letter={"attempted": False, "published": None},
        events=(),
        duration_s=float(checkpoint.get("elapsed_s", 0.0)),
        warning=_PARTIAL_SEND_WARNING if side_effect_unknown else SIDE_EFFECT_WARNING,
        failure=(
            {"failure_kind": "error", "exception_type": failure_type}
            if failure_type is not None
            else None
        ),
        failure_stage=str(checkpoint.get("phase", "child_start")),
        cleanup=cleanup,
        side_effect_outcome=("unknown" if side_effect_unknown else "not_attempted"),
        last_checkpoint=dict(checkpoint),
    )


__all__ = ["diagnostic_worker_main"]
