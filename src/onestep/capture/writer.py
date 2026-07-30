from __future__ import annotations

import asyncio
import copy
import json
import os
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from onestep.envelope import Envelope
from onestep.retry import FailureInfo

from .codec import decode_value, encode_value
from .config import FailureCaptureConfig

CAPTURE_SCHEMA = "onestep/envelope-capture"
CAPTURE_VERSION = 1
_REDACTED = "<redacted>"
_SECRET_KEYS = {
    "password",
    "passwd",
    "secret",
    "token",
    "authorization",
    "cookie",
    "api_key",
    "apikey",
    "dsn",
    "database_url",
    "connection_string",
}


@dataclass(frozen=True)
class LoadedCapture:
    app: str
    task: str
    stage: str
    terminal: bool
    failure: dict[str, Any]
    envelope: Envelope
    redacted_paths: tuple[str, ...]
    captured_at: str


def _escape_pointer_token(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _redact_secret_keys(value: Any, *, path: str, redacted: set[str]) -> Any:
    if isinstance(value, dict):
        result: dict[Any, Any] = {}
        for key, item in value.items():
            child_path = f"{path}/{_escape_pointer_token(str(key))}"
            normalized = (
                key.casefold().replace("-", "_") if isinstance(key, str) else ""
            )
            if normalized in _SECRET_KEYS:
                result[key] = _REDACTED
                redacted.add(child_path)
            else:
                result[key] = _redact_secret_keys(
                    item,
                    path=child_path,
                    redacted=redacted,
                )
        return result
    if isinstance(value, list):
        return [
            _redact_secret_keys(
                item,
                path=f"{path}/{index}",
                redacted=redacted,
            )
            for index, item in enumerate(value)
        ]
    if isinstance(value, tuple):
        items = [
            _redact_secret_keys(
                item,
                path=f"{path}/{index}",
                redacted=redacted,
            )
            for index, item in enumerate(value)
        ]
        if hasattr(type(value), "_fields"):
            return type(value)(*items)
        return tuple(items)
    if isinstance(value, set):
        return {
            _redact_secret_keys(item, path=path, redacted=redacted) for item in value
        }
    if isinstance(value, frozenset):
        return frozenset(
            _redact_secret_keys(item, path=path, redacted=redacted) for item in value
        )
    return value


def _decode_pointer(pointer: str) -> list[str]:
    if not pointer.startswith("/"):
        raise ValueError(f"invalid JSON Pointer {pointer!r}")
    tokens: list[str] = []
    for raw in pointer[1:].split("/"):
        token = ""
        index = 0
        while index < len(raw):
            if raw[index] != "~":
                token += raw[index]
                index += 1
                continue
            if index + 1 >= len(raw) or raw[index + 1] not in {"0", "1"}:
                raise ValueError(f"invalid JSON Pointer escape in {pointer!r}")
            token += "~" if raw[index + 1] == "0" else "/"
            index += 2
        tokens.append(token)
    return tokens


def _replace_tokens(value: Any, tokens: list[str], replacement: Any) -> tuple[Any, bool]:
    if not tokens:
        return replacement, True
    token, *remaining = tokens
    if isinstance(value, dict):
        if token not in value:
            return value, False
        replaced, changed = _replace_tokens(value[token], remaining, replacement)
        if not changed:
            return value, False
        result = dict(value)
        result[token] = replaced
        return result, True
    if isinstance(value, (list, tuple)):
        try:
            index = int(token)
        except ValueError:
            return value, False
        if index < 0 or index >= len(value) or str(index) != token:
            return value, False
        replaced, changed = _replace_tokens(value[index], remaining, replacement)
        if not changed:
            return value, False
        result_items = list(value)
        result_items[index] = replaced
        if isinstance(value, tuple) and hasattr(type(value), "_fields"):
            return type(value)(*result_items), True
        if isinstance(value, tuple):
            return tuple(result_items), True
        return result_items, True
    return value, False


def redact_envelope(
    envelope: Envelope,
    pointers: tuple[str, ...],
) -> tuple[Envelope, tuple[str, ...]]:
    logical = {
        "body": copy.deepcopy(envelope.body),
        "meta": copy.deepcopy(envelope.meta),
    }
    redacted: set[str] = set()
    logical = _redact_secret_keys(logical, path="", redacted=redacted)
    for pointer in pointers:
        logical, changed = _replace_tokens(
            logical,
            _decode_pointer(pointer),
            _REDACTED,
        )
        if changed:
            redacted.add(pointer)
    return (
        Envelope(
            body=logical["body"],
            meta=logical["meta"],
            attempts=envelope.attempts,
        ),
        tuple(sorted(redacted)),
    )


def _ensure_private_directory(path: Path) -> None:
    absolute = path.absolute()
    missing: list[Path] = []
    current = absolute
    while True:
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            missing.append(current)
        else:
            if stat.S_ISLNK(info.st_mode):
                raise ValueError(f"capture directory contains a symbolic link: {current}")
            if not stat.S_ISDIR(info.st_mode):
                raise NotADirectoryError(current)
            break
        parent = current.parent
        if parent == current:
            raise FileNotFoundError(current)
        current = parent
    for directory in reversed(missing):
        os.mkdir(directory, 0o700)
        os.chmod(directory, 0o700)


def _validate_scalar(value: Any, field: str, expected: type[Any]) -> Any:
    if not isinstance(value, expected):
        raise ValueError(f"capture field {field!r} has invalid type")
    return value


class FailureCaptureWriter:
    def __init__(self, config: FailureCaptureConfig) -> None:
        self.config = config

    async def write(
        self,
        *,
        app: str,
        task: str,
        stage: str,
        terminal: bool,
        failure: FailureInfo,
        envelope: Envelope,
    ) -> Path:
        return await asyncio.to_thread(
            self._write_sync,
            app=app,
            task=task,
            stage=stage,
            terminal=terminal,
            failure=failure,
            envelope=envelope,
        )

    def _write_sync(
        self,
        *,
        app: str,
        task: str,
        stage: str,
        terminal: bool,
        failure: FailureInfo,
        envelope: Envelope,
    ) -> Path:
        directory = self.config.directory
        _ensure_private_directory(directory)
        redacted_envelope, redacted_paths = redact_envelope(
            envelope,
            self.config.redact_paths,
        )
        captured_at = datetime.now(timezone.utc)
        document = {
            "schema": CAPTURE_SCHEMA,
            "version": CAPTURE_VERSION,
            "captured_at": captured_at.isoformat().replace("+00:00", "Z"),
            "app": app,
            "task": task,
            "stage": stage,
            "terminal": terminal,
            "failure": {
                "kind": failure.kind.value,
                "exception_type": failure.exception_type,
                "message": failure.message,
            },
            "envelope": {
                "body": encode_value(redacted_envelope.body),
                "meta": encode_value(redacted_envelope.meta),
                "attempts": redacted_envelope.attempts,
            },
            "redacted_paths": list(redacted_paths),
        }
        data = json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        ).encode("utf-8")
        if len(data) > self.config.max_bytes:
            raise ValueError(
                f"capture exceeds max_bytes ({len(data)} > {self.config.max_bytes})"
            )

        for _attempt in range(10):
            filename = f"{captured_at.strftime('%Y%m%dT%H%M%S%fZ')}-{uuid4().hex}.json"
            final_path = directory / filename
            try:
                os.lstat(final_path)
            except FileNotFoundError:
                pass
            else:
                continue
            temp_path = directory / f".{filename}.{uuid4().hex}.tmp"
            flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
            fd = -1
            try:
                fd = os.open(temp_path, flags, 0o600)
                descriptor_info = os.fstat(fd)
                path_info = os.lstat(temp_path)
                if not stat.S_ISREG(descriptor_info.st_mode) or (
                    descriptor_info.st_dev,
                    descriptor_info.st_ino,
                ) != (path_info.st_dev, path_info.st_ino):
                    raise ValueError("capture temporary path is not a regular file")
                with os.fdopen(fd, "wb") as handle:
                    fd = -1
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                try:
                    target_info = os.lstat(final_path)
                except FileNotFoundError:
                    target_info = None
                if target_info is not None:
                    if stat.S_ISLNK(target_info.st_mode):
                        raise ValueError("capture target is a symbolic link")
                    continue
                os.replace(temp_path, final_path)
                os.chmod(final_path, 0o600)
                directory_fd = os.open(directory, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
                return final_path
            finally:
                if fd >= 0:
                    os.close(fd)
                try:
                    os.unlink(temp_path)
                except FileNotFoundError:
                    pass
        raise FileExistsError("could not allocate a unique capture filename")


def load_capture(
    path: str | Path,
    *,
    expected_app: str | None = None,
    expected_task: str | None = None,
) -> LoadedCapture:
    capture_path = Path(path)
    raw = json.loads(capture_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("capture document must be an object")
    expected_fields = {
        "schema",
        "version",
        "captured_at",
        "app",
        "task",
        "stage",
        "terminal",
        "failure",
        "envelope",
        "redacted_paths",
    }
    if set(raw) != expected_fields:
        raise ValueError("capture document fields are invalid")
    if raw["schema"] != CAPTURE_SCHEMA:
        raise ValueError(
            f"unsupported capture schema {raw['schema']!r}; expected {CAPTURE_SCHEMA!r}"
        )
    if raw["version"] != CAPTURE_VERSION:
        raise ValueError(
            f"unsupported capture version {raw['version']!r}; expected {CAPTURE_VERSION}"
        )
    app = _validate_scalar(raw["app"], "app", str)
    task = _validate_scalar(raw["task"], "task", str)
    stage = _validate_scalar(raw["stage"], "stage", str)
    captured_at = _validate_scalar(raw["captured_at"], "captured_at", str)
    try:
        datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("capture field 'captured_at' is invalid") from exc
    if not isinstance(raw["terminal"], bool):
        raise ValueError("capture field 'terminal' has invalid type")
    if expected_app is not None and app != expected_app:
        raise ValueError(f"capture app mismatch: expected {expected_app!r}, received {app!r}")
    if expected_task is not None and task != expected_task:
        raise ValueError(
            f"capture task mismatch: expected {expected_task!r}, received {task!r}"
        )

    failure = raw["failure"]
    if not isinstance(failure, dict) or set(failure) != {
        "kind",
        "exception_type",
        "message",
    }:
        raise ValueError("capture failure fields are invalid")
    if not all(isinstance(value, str) for value in failure.values()):
        raise ValueError("capture failure fields must be strings")
    envelope = raw["envelope"]
    if not isinstance(envelope, dict) or set(envelope) != {"body", "meta", "attempts"}:
        raise ValueError("capture envelope fields are invalid")
    attempts = envelope["attempts"]
    if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 0:
        raise ValueError("capture envelope attempts must be a non-negative integer")
    decoded_meta = decode_value(envelope["meta"])
    if not isinstance(decoded_meta, dict):
        raise ValueError("capture envelope meta must decode to a mapping")
    redacted_paths = raw["redacted_paths"]
    if not isinstance(redacted_paths, list) or not all(
        isinstance(item, str) and item.startswith("/") for item in redacted_paths
    ):
        raise ValueError("capture redacted_paths must be JSON Pointer strings")
    return LoadedCapture(
        app=app,
        task=task,
        stage=stage,
        terminal=raw["terminal"],
        failure=dict(failure),
        envelope=Envelope(
            body=decode_value(envelope["body"]),
            meta=decoded_meta,
            attempts=attempts,
        ),
        redacted_paths=tuple(redacted_paths),
        captured_at=captured_at,
    )


__all__ = [
    "CAPTURE_SCHEMA",
    "CAPTURE_VERSION",
    "FailureCaptureWriter",
    "LoadedCapture",
    "load_capture",
    "redact_envelope",
]
