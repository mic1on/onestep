from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Any, Literal

from .models import DiagnosticReport

IPC_SCHEMA = "onestep/diagnostic-ipc"
IPC_VERSION = 1
STATUS_KINDS = frozenset({"checkpoint", "final"})
CONTROL_KINDS = frozenset({"cancel"})
_ALL_KINDS = STATUS_KINDS | CONTROL_KINDS
_CHECKPOINT_FIELDS = frozenset(
    {
        "phase",
        "transition",
        "elapsed_s",
        "app",
        "task",
        "resource",
        "selected_sinks",
        "completion",
        "cleanup",
    }
)


class IPCProtocolError(ValueError):
    pass


def encode_frame(
    kind: str,
    *,
    sequence: int,
    payload: Mapping[str, Any],
) -> bytes:
    frame = {
        "schema": IPC_SCHEMA,
        "version": IPC_VERSION,
        "kind": kind,
        "sequence": sequence,
        "payload": dict(payload),
    }
    return json.dumps(frame, ensure_ascii=True, separators=(",", ":")).encode(
        "utf-8"
    )


def decode_frame(data: bytes) -> dict[str, Any]:
    try:
        frame = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IPCProtocolError("malformed JSON frame") from exc
    if not isinstance(frame, dict):
        raise IPCProtocolError("frame must be an object")
    if set(frame) != {"schema", "version", "kind", "sequence", "payload"}:
        raise IPCProtocolError("frame fields are invalid")
    if frame["schema"] != IPC_SCHEMA or frame["version"] != IPC_VERSION:
        raise IPCProtocolError("unsupported IPC schema or version")
    if frame["kind"] not in _ALL_KINDS:
        raise IPCProtocolError("invalid IPC kind")
    return frame


class FrameValidator:
    def __init__(self, *, direction: Literal["status", "control"]) -> None:
        if direction not in {"status", "control"}:
            raise ValueError("IPC direction must be status or control")
        self.direction = direction
        self.last_sequence = 0

    def accept(self, frame: Mapping[str, Any]) -> dict[str, Any]:
        if set(frame) != {"schema", "version", "kind", "sequence", "payload"}:
            raise IPCProtocolError("frame fields are invalid")
        if frame["schema"] != IPC_SCHEMA or frame["version"] != IPC_VERSION:
            raise IPCProtocolError("unsupported IPC schema or version")
        sequence = frame["sequence"]
        if (
            isinstance(sequence, bool)
            or not isinstance(sequence, int)
            or sequence <= self.last_sequence
        ):
            raise IPCProtocolError("non-monotonic IPC sequence")
        allowed = STATUS_KINDS if self.direction == "status" else CONTROL_KINDS
        if frame["kind"] not in allowed:
            raise IPCProtocolError("invalid IPC kind for direction")
        payload = _validate_payload(frame["kind"], frame["payload"])
        self.last_sequence = sequence
        return payload


def _validate_payload(kind: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise IPCProtocolError("IPC payload must be an object")
    if kind == "cancel":
        if value:
            raise IPCProtocolError("cancel payload must be empty")
        return {}
    if kind == "final":
        try:
            DiagnosticReport.from_dict(value)
        except (KeyError, TypeError, ValueError) as exc:
            raise IPCProtocolError("final payload is not a diagnostic result") from exc
        return dict(value)
    if kind != "checkpoint":
        raise IPCProtocolError("invalid IPC payload kind")
    if not set(value).issubset(_CHECKPOINT_FIELDS):
        raise IPCProtocolError("checkpoint fields are invalid")
    if not isinstance(value.get("phase"), str) or not value["phase"]:
        raise IPCProtocolError("checkpoint phase must be a non-empty string")
    if value.get("transition") not in {"entered", "completed"}:
        raise IPCProtocolError("checkpoint transition is invalid")
    elapsed = value.get("elapsed_s")
    if (
        isinstance(elapsed, bool)
        or not isinstance(elapsed, (int, float))
        or elapsed < 0
        or not math.isfinite(float(elapsed))
    ):
        raise IPCProtocolError("checkpoint elapsed_s is invalid")
    for field in ("app", "task", "resource", "completion", "cleanup"):
        if field in value and value[field] is not None and not isinstance(
            value[field], str
        ):
            raise IPCProtocolError(f"checkpoint {field} must be a string")
    selected = value.get("selected_sinks")
    if selected is not None and (
        not isinstance(selected, list)
        or any(not isinstance(item, str) for item in selected)
    ):
        raise IPCProtocolError("checkpoint selected_sinks must be strings")
    return dict(value)


__all__ = [
    "CONTROL_KINDS",
    "IPC_SCHEMA",
    "IPC_VERSION",
    "STATUS_KINDS",
    "FrameValidator",
    "IPCProtocolError",
    "decode_frame",
    "encode_frame",
]
