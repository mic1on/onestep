from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Mapping

from onestep.capture.codec import decode_value, encode_value
from onestep.envelope import Envelope
from onestep.events import TaskEvent, TaskEventKind
from onestep.retry import FailureInfo, FailureKind

DIAGNOSTIC_SCHEMA = "onestep/diagnostic-result"
DIAGNOSTIC_VERSION = 1
CONNECTIVITY_SCHEMA = "onestep/connectivity-result"
CONNECTIVITY_VERSION = 1
SIDE_EFFECT_WARNING = "handler and task hooks may perform external side effects"


@dataclass(frozen=True)
class DiagnosticRequest:
    operation: Literal["run", "replay"]
    target: str
    task: str
    envelope: Envelope | None = None
    capture_path: str | None = None
    send: bool = False

    def __post_init__(self) -> None:
        if self.operation == "run":
            if self.envelope is None or self.capture_path is not None:
                raise ValueError("run request requires an envelope only")
        elif self.operation == "replay":
            if self.capture_path is None or self.envelope is not None:
                raise ValueError("replay request requires a capture path only")
        else:
            raise ValueError(f"unsupported diagnostic operation {self.operation!r}")


@dataclass(frozen=True)
class DiagnosticReport:
    operation: str
    app: str
    task: str
    mode: str
    completion: str
    attempts: int
    selected_sinks: tuple[str, ...]
    delivery_action: str | None
    delivery_action_basis: str
    dead_letter: dict[str, bool | None]
    events: tuple[TaskEvent, ...]
    duration_s: float
    outputs: tuple[dict[str, Any], ...] = ()
    warning: str = SIDE_EFFECT_WARNING
    failure: dict[str, str] | None = None
    failure_stage: str | None = None
    cleanup: str = "complete"
    side_effect_outcome: str = "not_attempted"
    last_checkpoint: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": DIAGNOSTIC_SCHEMA,
            "version": DIAGNOSTIC_VERSION,
            "operation": self.operation,
            "app": self.app,
            "task": self.task,
            "mode": self.mode,
            "completion": self.completion,
            "attempts": self.attempts,
            "selected_sinks": list(self.selected_sinks),
            "delivery_action": self.delivery_action,
            "delivery_action_basis": self.delivery_action_basis,
            "dead_letter": dict(self.dead_letter),
            "events": [_event_to_dict(event) for event in self.events],
            "duration_s": self.duration_s,
            "outputs": [dict(item) for item in self.outputs],
            "warning": self.warning,
            "failure": dict(self.failure) if self.failure is not None else None,
            "failure_stage": self.failure_stage,
            "cleanup": self.cleanup,
            "side_effect_outcome": self.side_effect_outcome,
            "last_checkpoint": (
                dict(self.last_checkpoint)
                if self.last_checkpoint is not None
                else None
            ),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DiagnosticReport":
        _validate_diagnostic_result(value)
        return cls(
            operation=value["operation"],
            app=value["app"],
            task=value["task"],
            mode=value["mode"],
            completion=value["completion"],
            attempts=value["attempts"],
            selected_sinks=tuple(value["selected_sinks"]),
            delivery_action=value["delivery_action"],
            delivery_action_basis=value["delivery_action_basis"],
            dead_letter=dict(value["dead_letter"]),
            events=tuple(_event_from_dict(item) for item in value["events"]),
            duration_s=value["duration_s"],
            outputs=tuple(dict(item) for item in value["outputs"]),
            warning=value["warning"],
            failure=dict(value["failure"]) if value["failure"] is not None else None,
            failure_stage=value["failure_stage"],
            cleanup=value["cleanup"],
            side_effect_outcome=value["side_effect_outcome"],
            last_checkpoint=(
                dict(value["last_checkpoint"])
                if value["last_checkpoint"] is not None
                else None
            ),
        )


@dataclass(frozen=True)
class ConnectivityResourceResult:
    aliases: tuple[str, ...]
    roles: tuple[str, ...]
    type_name: str
    probe_kind: str
    status: str
    open: dict[str, Any] | None = None
    close: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "aliases": list(self.aliases),
            "roles": list(self.roles),
            "type": self.type_name,
            "probe_kind": self.probe_kind,
            "status": self.status,
            "open": dict(self.open) if self.open is not None else None,
            "close": dict(self.close) if self.close is not None else None,
        }


@dataclass(frozen=True)
class ConnectivityReport:
    app: str
    resources: tuple[ConnectivityResourceResult, ...]
    ok: bool
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": CONNECTIVITY_SCHEMA,
            "version": CONNECTIVITY_VERSION,
            "app": self.app,
            "ok": self.ok,
            "warnings": list(self.warnings),
            "resources": [resource.to_dict() for resource in self.resources],
        }


def _event_to_dict(event: TaskEvent) -> dict[str, Any]:
    failure = None
    if event.failure is not None:
        failure = {
            "kind": event.failure.kind.value,
            "exception_type": event.failure.exception_type,
        }
    return {
        "kind": event.kind.value,
        "app": event.app,
        "task": event.task,
        "source": event.source,
        "attempts": event.attempts,
        "emitted_at": event.emitted_at.isoformat(),
        "duration_s": event.duration_s,
        "failure": failure,
        "meta": encode_value(event.meta),
    }


def _event_from_dict(value: Any) -> TaskEvent:
    if not isinstance(value, Mapping):
        raise ValueError("diagnostic event must be an object")
    failure_value = value.get("failure")
    failure = None
    if failure_value is not None:
        if not isinstance(failure_value, Mapping):
            raise ValueError("diagnostic event failure must be an object")
        failure = FailureInfo(
            kind=FailureKind(failure_value["kind"]),
            exception_type=failure_value["exception_type"],
            message="",
        )
    emitted_at = datetime.fromisoformat(value["emitted_at"])
    meta = decode_value(value["meta"])
    if not isinstance(meta, dict):
        raise ValueError("diagnostic event meta must decode to a mapping")
    return TaskEvent(
        kind=TaskEventKind(value["kind"]),
        app=value["app"],
        task=value["task"],
        source=value["source"],
        attempts=value["attempts"],
        emitted_at=emitted_at,
        duration_s=value["duration_s"],
        failure=failure,
        meta=meta,
    )


def _validate_diagnostic_result(value: Mapping[str, Any]) -> None:
    if value.get("schema") != DIAGNOSTIC_SCHEMA:
        raise ValueError("unsupported diagnostic result schema")
    if value.get("version") != DIAGNOSTIC_VERSION:
        raise ValueError("unsupported diagnostic result version")
    required = {
        "schema",
        "version",
        "operation",
        "app",
        "task",
        "mode",
        "completion",
        "attempts",
        "selected_sinks",
        "delivery_action",
        "delivery_action_basis",
        "dead_letter",
        "events",
        "duration_s",
        "outputs",
        "warning",
        "failure",
        "failure_stage",
        "cleanup",
        "side_effect_outcome",
        "last_checkpoint",
    }
    if set(value) != required:
        raise ValueError("diagnostic result fields are invalid")
    attempts = value["attempts"]
    duration = value["duration_s"]
    if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 0:
        raise ValueError("diagnostic attempts must be a non-negative integer")
    if isinstance(duration, bool) or not isinstance(duration, (int, float)):
        raise ValueError("diagnostic duration must be numeric")
    if duration < 0 or not math.isfinite(float(duration)):
        raise ValueError("diagnostic duration must be finite and non-negative")
    if value["completion"] not in {
        "succeeded",
        "failed",
        "timed_out",
        "child_failed",
        "cancelled",
    }:
        raise ValueError("diagnostic completion is invalid")
    if not isinstance(value["selected_sinks"], list):
        raise ValueError("diagnostic selected_sinks must be a list")
    if not isinstance(value["events"], list) or not isinstance(value["outputs"], list):
        raise ValueError("diagnostic events and outputs must be lists")


def encode_request(request: DiagnosticRequest) -> bytes:
    document: dict[str, Any] = {
        "schema": "onestep/diagnostic-request",
        "version": 1,
        "operation": request.operation,
        "target": request.target,
        "task": request.task,
        "send": request.send,
        "capture_path": request.capture_path,
        "envelope": None,
    }
    if request.envelope is not None:
        document["envelope"] = {
            "body": encode_value(request.envelope.body),
            "meta": encode_value(request.envelope.meta),
            "attempts": request.envelope.attempts,
        }
    return json.dumps(document, ensure_ascii=True, separators=(",", ":")).encode(
        "utf-8"
    )


def decode_request(data: bytes) -> DiagnosticRequest:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("malformed diagnostic request") from exc
    if not isinstance(value, dict) or set(value) != {
        "schema",
        "version",
        "operation",
        "target",
        "task",
        "send",
        "capture_path",
        "envelope",
    }:
        raise ValueError("diagnostic request fields are invalid")
    if value["schema"] != "onestep/diagnostic-request" or value["version"] != 1:
        raise ValueError("unsupported diagnostic request schema or version")
    if not isinstance(value["target"], str) or not isinstance(value["task"], str):
        raise ValueError("diagnostic request target and task must be strings")
    if not isinstance(value["send"], bool):
        raise ValueError("diagnostic request send must be boolean")
    envelope = None
    if value["envelope"] is not None:
        raw_envelope = value["envelope"]
        if not isinstance(raw_envelope, dict) or set(raw_envelope) != {
            "body",
            "meta",
            "attempts",
        }:
            raise ValueError("diagnostic request envelope is invalid")
        attempts = raw_envelope["attempts"]
        if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 0:
            raise ValueError("diagnostic request attempts must be non-negative")
        meta = decode_value(raw_envelope["meta"])
        if not isinstance(meta, dict):
            raise ValueError("diagnostic request meta must decode to a mapping")
        envelope = Envelope(
            body=decode_value(raw_envelope["body"]),
            meta=meta,
            attempts=attempts,
        )
    capture_path = value["capture_path"]
    if capture_path is not None and not isinstance(capture_path, str):
        raise ValueError("diagnostic request capture_path must be a string")
    return DiagnosticRequest(
        operation=value["operation"],
        target=value["target"],
        task=value["task"],
        envelope=envelope,
        capture_path=capture_path,
        send=value["send"],
    )


__all__ = [
    "CONNECTIVITY_SCHEMA",
    "CONNECTIVITY_VERSION",
    "DIAGNOSTIC_SCHEMA",
    "DIAGNOSTIC_VERSION",
    "SIDE_EFFECT_WARNING",
    "ConnectivityReport",
    "ConnectivityResourceResult",
    "DiagnosticReport",
    "DiagnosticRequest",
    "decode_request",
    "encode_request",
]
