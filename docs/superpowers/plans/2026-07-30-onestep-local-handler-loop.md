# onestep Local Handler Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add safe local task run/replay diagnostics, capability-based connectivity checks, and opt-in lossless failure capture without changing production delivery, remote-control, reporter, or Control Plane contracts.

**Architecture:** Extract one-delivery behavior from `TaskRunner` into an internal `DeliveryExecutor` with injected event, sink, delivery-action, checkpoint, and capture collaborators. Build diagnostics as a separate package: an in-child `DiagnosticRunner`, a connectivity checker, a versioned JSON-over-pipe IPC layer, and a parent supervisor that enforces the overall timeout. Keep capture persistence in a focused package with public configuration and internal codec/writer modules.

**Tech Stack:** Python 3.9+, asyncio, multiprocessing `spawn`, standard-library JSON/filesystem primitives, argparse, pytest, pytest-asyncio, uv workspace

---

## File Map

- Create `src/onestep/capture/__init__.py`: capture package exports.
- Create `src/onestep/capture/config.py`: public `FailureCaptureConfig` validation.
- Create `src/onestep/capture/codec.py`: collision-safe lossless value encoding and decoding.
- Create `src/onestep/capture/writer.py`: redaction, versioned capture validation, private atomic persistence, and replay loading.
- Create `src/onestep/runtime/executor.py`: internal single-delivery execution and production-compatible collaborators.
- Modify `src/onestep/runtime/runner.py`: retain polling and runner state; delegate `_handle_delivery()` to `DeliveryExecutor`.
- Modify `src/onestep/app.py`: accept failure-capture policy and preserve `run_task_once()` behavior through the delegated runner path.
- Modify `src/onestep/config.py`: build and strictly validate `app.failure_capture`.
- Modify `src/onestep/__init__.py`: export only the stable `FailureCaptureConfig` addition.
- Create `src/onestep/diagnostics/__init__.py`: internal diagnostic package marker.
- Create `src/onestep/diagnostics/models.py`: diagnostic request/result/checkpoint models and report serialization.
- Create `src/onestep/diagnostics/runner.py`: synthetic delivery, dry-run/send policies, local events, and sink cleanup.
- Create `src/onestep/diagnostics/connectivity.py`: resource inventory and capability-based lifecycle probes.
- Create `src/onestep/diagnostics/ipc.py`: private versioned JSON frame encoding/validation.
- Create `src/onestep/diagnostics/targets.py`: shared Python/YAML target loading and local import-path setup for parent and child.
- Create `src/onestep/diagnostics/worker.py`: spawned child entry point and cooperative cancellation bridge.
- Create `src/onestep/diagnostics/supervisor.py`: parent process deadline, checkpoint retention, termination, and report synthesis.
- Modify `src/onestep/cli.py`: nested `task` parsers, argv normalization, `check --connect`, supervision calls, rendering, and exit codes.
- Create `tests/test_failure_capture.py`: config, codec, redaction, writer, and replay validation tests.
- Create `tests/test_diagnostics.py`: executor adapters, diagnostic runner, connectivity, IPC, and supervisor tests.
- Create `tests/assets/diagnostic_app.py`: importable Python target for real spawned-process tests.
- Modify `tests/contract/test_runtime_contract.py`: characterize production behavior before and after extraction and cover capture timing.
- Modify `tests/test_cli.py`: parser, normalization, command, report, and exit-code coverage.
- Modify `tests/test_config_env.py`: strict YAML failure-capture validation.
- Modify `tests/test_packaging.py`: stable export coverage for `FailureCaptureConfig`.
- Modify `README.md` and `README.zh-CN.md`: local loop, timeout, side effects, capture, and connectivity documentation.
- Modify `docs/yaml-task-definition.md`: `app.failure_capture` schema and supported capture values.
- Modify `docs/framework-evolution-roadmap.md`: mark P2 complete only after all gates pass.
- Modify `CHANGELOG.md`, `pyproject.toml`, and `uv.lock`: prepare core `1.8.0` release metadata.

## Task 1: Lock Existing Production Delivery Semantics

**Files:**
- Modify: `tests/contract/test_runtime_contract.py`

- [ ] **Step 1: Add an explicit success-order characterization test**

Add a local delivery and sink that append to one ordered list, then invoke the
current compatibility entry point directly:

```python
def test_single_delivery_success_order_is_stable_contract() -> None:
    class RecordingDelivery(Delivery):
        def __init__(self, steps: list[str]) -> None:
            super().__init__(Envelope(body={"value": 2}, meta={"trace": "t-1"}))
            self.steps = steps

        async def start_processing(self) -> None:
            self.steps.append("start_processing")

        async def ack(self) -> None:
            self.steps.append("ack")

        async def retry(self, *, delay_s: float | None = None) -> None:
            self.steps.append("retry")

        async def fail(self, exc: Exception | None = None) -> None:
            self.steps.append("fail")

    class RecordingSink(Sink):
        def __init__(self, steps: list[str]) -> None:
            super().__init__("recording")
            self.steps = steps

        async def send(self, envelope: Envelope) -> None:
            assert envelope.body == {"value": 4}
            self.steps.append("sink")

    async def scenario() -> None:
        steps: list[str] = []
        app = OneStepApp("execution-order")
        sink = RecordingSink(steps)

        @app.on_event
        def event(event):
            steps.append(f"event:{event.kind.value}")

        async def before(ctx, payload):
            steps.append("before")

        async def after(ctx, payload, result):
            steps.append("after_success")

        @app.task(emit=sink, hooks=TaskHooks(before=(before,), after_success=(after,)))
        async def consume(ctx, payload):
            steps.append("handler")
            return {"value": payload["value"] * 2}

        await TaskRunner(app, app.tasks[0])._handle_delivery(RecordingDelivery(steps))

        assert steps == [
            "start_processing",
            "event:started",
            "before",
            "handler",
            "after_success",
            "sink",
            "ack",
            "event:succeeded",
        ]

    asyncio.run(scenario())
```

Import `TaskHooks` and `TaskRunner` from their current modules at the top of the
test file.

- [ ] **Step 2: Add a fallback-retry characterization test**

Add a delivery whose `fail()` raises and whose `retry()` records the fallback.
Use `NoRetry()` and a successful dead-letter sink; assert event order remains
`started`, `dead_lettered`, `failed`, while the delivery action order is
`fail`, `retry`. This deliberately locks the current public event semantics even
though the terminal classification used by capture must be non-terminal.

- [ ] **Step 3: Run the characterization slice**

```bash
uv run pytest tests/contract/test_runtime_contract.py -k 'single_delivery_success_order or fail_action_fallback' -v
```

Expected: both tests pass against the pre-refactor runtime.

- [ ] **Step 4: Commit the contract tests**

```bash
git add tests/contract/test_runtime_contract.py
git commit -m "test: characterize single delivery execution"
```

## Task 2: Add Failure Capture Configuration

**Files:**
- Create: `src/onestep/capture/__init__.py`
- Create: `src/onestep/capture/config.py`
- Modify: `src/onestep/app.py`
- Modify: `src/onestep/config.py`
- Modify: `src/onestep/__init__.py`
- Test: `tests/test_failure_capture.py`
- Test: `tests/test_config_env.py`
- Test: `tests/test_packaging.py`

- [ ] **Step 1: Write failing Python and YAML configuration tests**

Create `tests/test_failure_capture.py` with:

```python
from pathlib import Path

import pytest

from onestep import FailureCaptureConfig
from onestep.config import load_app_config


def test_failure_capture_config_normalizes_values(tmp_path: Path) -> None:
    config = FailureCaptureConfig(
        directory=tmp_path / "captures",
        mode="all",
        max_bytes=2048,
        redact_paths=("/body/password",),
    )
    assert config.directory == tmp_path / "captures"
    assert config.mode == "all"
    assert config.max_bytes == 2048
    assert config.redact_paths == ("/body/password",)


@pytest.mark.parametrize(
    "kwargs,match",
    [
        ({"mode": "sometimes"}, "mode must be 'terminal' or 'all'"),
        ({"max_bytes": 0}, "max_bytes must be >= 1"),
        ({"redact_paths": ("body/password",)}, "JSON Pointer"),
        ({"redact_paths": ("",)}, "JSON Pointer"),
    ],
)
def test_failure_capture_config_rejects_invalid_values(kwargs, match) -> None:
    with pytest.raises((TypeError, ValueError), match=match):
        FailureCaptureConfig(directory="captures", **kwargs)


def test_yaml_app_builds_failure_capture_config(tmp_path: Path) -> None:
    app = load_app_config(
        {
            "app": {
                "name": "capture-yaml",
                "failure_capture": {
                    "directory": str(tmp_path / "captures"),
                    "mode": "terminal",
                    "max_bytes": 4096,
                    "redact_paths": ["/body/token"],
                },
            },
            "tasks": [],
        },
        strict=True,
    )
    assert app.failure_capture == FailureCaptureConfig(
        directory=tmp_path / "captures",
        mode="terminal",
        max_bytes=4096,
        redact_paths=("/body/token",),
    )
```

Add strict-YAML cases to `tests/test_config_env.py` for an unknown nested field,
non-string directory, invalid mode, non-positive max bytes, and non-list
`redact_paths`. Add `FailureCaptureConfig` to the public export assertion in
`tests/test_packaging.py`.

- [ ] **Step 2: Run the focused tests and confirm missing API/schema failures**

```bash
uv run pytest tests/test_failure_capture.py tests/test_config_env.py tests/test_packaging.py -k 'failure_capture or public_api' -v
```

Expected: collection fails because `FailureCaptureConfig` is not exported.

- [ ] **Step 3: Implement the validated config type**

Create `src/onestep/capture/config.py`:

```python
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

CaptureMode = Literal["terminal", "all"]


@dataclass(frozen=True)
class FailureCaptureConfig:
    directory: Path | str
    mode: CaptureMode = "terminal"
    max_bytes: int = 1_048_576
    redact_paths: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.directory, (str, Path)):
            raise TypeError("failure_capture.directory must be a path string")
        if self.mode not in {"terminal", "all"}:
            raise ValueError("failure_capture.mode must be 'terminal' or 'all'")
        if isinstance(self.max_bytes, bool) or not isinstance(self.max_bytes, int):
            raise TypeError("failure_capture.max_bytes must be an integer")
        if self.max_bytes < 1:
            raise ValueError("failure_capture.max_bytes must be >= 1")
        paths = tuple(self.redact_paths)
        for path in paths:
            if not isinstance(path, str) or not path.startswith("/"):
                raise ValueError("failure_capture.redact_paths entries must be JSON Pointer paths")
        object.__setattr__(self, "directory", Path(self.directory))
        object.__setattr__(self, "redact_paths", paths)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "FailureCaptureConfig":
        allowed = {"directory", "mode", "max_bytes", "redact_paths"}
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise ValueError("unsupported fields for app.failure_capture: " + ", ".join(unknown))
        if "directory" not in value:
            raise ValueError("app.failure_capture.directory is required")
        paths = value.get("redact_paths", ())
        if not isinstance(paths, (list, tuple)) or isinstance(paths, (str, bytes)):
            raise TypeError("app.failure_capture.redact_paths must be a list")
        return cls(
            directory=value["directory"],
            mode=value.get("mode", "terminal"),
            max_bytes=value.get("max_bytes", 1_048_576),
            redact_paths=tuple(paths),
        )
```

Export it from `src/onestep/capture/__init__.py` and `src/onestep/__init__.py`.
Add keyword-only `failure_capture: FailureCaptureConfig | None = None` to
`OneStepApp.__init__` and assign `self.failure_capture`.

- [ ] **Step 4: Wire and strictly validate YAML**

Add `failure_capture` to `_STRICT_APP_FIELDS`. In `load_app_config()`, resolve:

```python
raw_failure_capture = app_section.get("failure_capture") if app_section is not None else None
failure_capture = None
if raw_failure_capture is not None:
    if not isinstance(raw_failure_capture, Mapping):
        raise TypeError("'app.failure_capture' must be a mapping")
    failure_capture = FailureCaptureConfig.from_mapping(raw_failure_capture)

app = OneStepApp(
    app_name,
    config=dict(app_config),
    shutdown_timeout_s=shutdown_timeout_s,
    failure_capture=failure_capture,
)
```

Call the same mapping validation from strict validation so `check --strict`
fails before app construction.

- [ ] **Step 5: Run config and export tests**

```bash
uv run pytest tests/test_failure_capture.py tests/test_config_env.py tests/test_packaging.py -k 'failure_capture or public_api' -v
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit configuration support**

```bash
git add src/onestep/capture src/onestep/app.py src/onestep/config.py src/onestep/__init__.py tests/test_failure_capture.py tests/test_config_env.py tests/test_packaging.py
git commit -m "feat: configure failure capture"
```

## Task 3: Implement The Lossless Capture Codec

**Files:**
- Create: `src/onestep/capture/codec.py`
- Modify: `src/onestep/capture/__init__.py`
- Test: `tests/test_failure_capture.py`

- [ ] **Step 1: Write codec round-trip and rejection tests**

Define module-level test types so they are importable during decode:

```python
from collections import namedtuple
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from uuid import UUID

from onestep.capture.codec import CaptureEncodingError, decode_value, encode_value


class CaptureStatus(Enum):
    READY = ("ready", 1)


CapturePoint = namedtuple("CapturePoint", "x y")


def test_capture_codec_round_trips_supported_values() -> None:
    value = {
        "at": datetime(2026, 7, 30, 9, 15, tzinfo=timezone.utc),
        "id": UUID("d78e3d0d-13e1-44ef-96a5-10bd28fc882d"),
        "raw": b"\x00\xff",
        "amount": Decimal("-0.0100"),
        "tuple": (1, "x"),
        "enum": CaptureStatus.READY,
        "point": CapturePoint(2, 3),
        "set": {"b", "a"},
        "frozen": frozenset({2, 1}),
        "$onestep": {"type": "user-data"},
    }
    assert decode_value(encode_value(value)) == value


def test_capture_codec_orders_sets_deterministically() -> None:
    assert encode_value({"b", "a", "c"}) == encode_value({"c", "b", "a"})


def test_capture_codec_rejects_custom_values_without_repr() -> None:
    class SecretObject:
        def __repr__(self) -> str:
            return "token=do-not-log"

    with pytest.raises(CaptureEncodingError) as captured:
        encode_value({"nested": [SecretObject()]})
    assert captured.value.path == "/nested/0"
    assert captured.value.type_name.endswith("SecretObject")
    assert "do-not-log" not in str(captured.value)
```

Create `test_capture_codec_rejects_unknown_tag()`,
`test_capture_codec_rejects_unloaded_enum_module()`,
`test_capture_codec_rejects_changed_enum_value()`,
`test_capture_codec_rejects_local_namedtuple()`,
`test_capture_codec_rejects_non_string_mapping_key()`, and
`test_capture_codec_rejects_non_finite_float()`. Each uses
`pytest.raises(CaptureEncodingError, match=...)` for encode errors or
`pytest.raises(ValueError, match=...)` for decode errors; every message asserts
the safe pointer/type and explicitly asserts secret test values are absent.

- [ ] **Step 2: Run tests and confirm the missing codec failure**

```bash
uv run pytest tests/test_failure_capture.py -k capture_codec -v
```

Expected: import fails because `onestep.capture.codec` does not exist.

- [ ] **Step 3: Implement collision-safe tagged encoding**

Create `src/onestep/capture/codec.py`. Use `$onestep` only for extension nodes;
ordinary dictionaries containing that key are escaped as a tagged mapping:

```python
from __future__ import annotations

import base64
import json
import math
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

TAG = "$onestep"


class CaptureEncodingError(ValueError):
    def __init__(self, *, path: str, type_name: str, reason: str) -> None:
        self.path = path or "/"
        self.type_name = type_name
        self.reason = reason
        super().__init__(f"cannot encode {type_name} at {self.path}: {reason}")


def _type_name(value: Any) -> str:
    cls = type(value)
    return f"{cls.__module__}.{cls.__qualname__}"


def _pointer(path: str, token: str) -> str:
    escaped = token.replace("~", "~0").replace("/", "~1")
    return f"{path}/{escaped}"


def _tag(type_name: str, **fields: Any) -> dict[str, Any]:
    return {TAG: {"type": type_name, **fields}}


def encode_value(value: Any, *, _path: str = "") -> Any:
    if isinstance(value, Enum):
        cls = type(value)
        return _tag(
            "enum",
            module=cls.__module__,
            qualname=cls.__qualname__,
            name=value.name,
            value=encode_value(value.value, _path=_pointer(_path, "value")),
        )
    if value is None or type(value) in {bool, int, str}:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise CaptureEncodingError(path=_path, type_name="builtins.float", reason="non-finite float")
        return value
    if type(value) is datetime:
        return _tag("datetime", value=value.isoformat(), fold=value.fold)
    if type(value) is UUID:
        return _tag("uuid", value=str(value))
    if type(value) is bytes:
        return _tag("bytes", value=base64.b64encode(value).decode("ascii"))
    if type(value) is Decimal:
        return _tag("decimal", value=str(value))
    if isinstance(value, tuple) and hasattr(type(value), "_fields"):
        cls = type(value)
        fields = tuple(getattr(cls, "_fields"))
        return _tag(
            "namedtuple",
            module=cls.__module__,
            qualname=cls.__qualname__,
            fields=list(fields),
            values=[encode_value(item, _path=_pointer(_path, field)) for field, item in zip(fields, value)],
        )
    if type(value) is tuple:
        return _tag("tuple", values=[encode_value(item, _path=_pointer(_path, str(i))) for i, item in enumerate(value)])
    if type(value) in {set, frozenset}:
        encoded = [encode_value(item, _path=_pointer(_path, "set")) for item in value]
        encoded.sort(key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=True))
        return _tag("frozenset" if type(value) is frozenset else "set", values=encoded)
    if type(value) is list:
        return [encode_value(item, _path=_pointer(_path, str(i))) for i, item in enumerate(value)]
    if type(value) is dict:
        for key in value:
            if not isinstance(key, str):
                raise CaptureEncodingError(path=_path, type_name=_type_name(key), reason="mapping keys must be strings")
        if TAG in value:
            return _tag("mapping", items=[[key, encode_value(item, _path=_pointer(_path, key))] for key, item in value.items()])
        return {key: encode_value(item, _path=_pointer(_path, key)) for key, item in value.items()}
    raise CaptureEncodingError(path=_path, type_name=_type_name(value), reason="unsupported value type")
```

Plain JSON branches accept exact built-in types only. Reject subclasses of
supported built-ins unless they use an explicit lossless extension such as enum
or namedtuple; decoding a custom subtype as its base type is forbidden.

- [ ] **Step 4: Implement strict decoding and type reconstruction**

In the same module, add `_resolve_loaded_type()` that rejects `<locals>`, requires
the module to already exist in `sys.modules`, walks `qualname`, and verifies the
resolved object is a type. Implement `decode_value()` for every tag above. For
enum, require `issubclass(cls, Enum)`, resolve by recorded member name, and
compare its value with the decoded recorded value. For namedtuple, require
`tuple(cls._fields) == tuple(recorded_fields)` before construction. Reject extra
tag fields rather than silently accepting malformed captures.

Use the exact public signature `decode_value(value: Any, *, _path: str = "") ->
Any` and export list `__all__ = ["CaptureEncodingError", "decode_value",
"encode_value"]`.

- [ ] **Step 5: Run codec tests**

```bash
uv run pytest tests/test_failure_capture.py -k capture_codec -v
```

Expected: all codec tests pass, including deterministic output and safe errors.

- [ ] **Step 6: Commit the codec**

```bash
git add src/onestep/capture tests/test_failure_capture.py
git commit -m "feat: add lossless capture codec"
```

## Task 4: Add Redacted Private Capture Persistence And Replay Loading

**Files:**
- Create: `src/onestep/capture/writer.py`
- Modify: `src/onestep/capture/__init__.py`
- Test: `tests/test_failure_capture.py`

- [ ] **Step 1: Write redaction and writer failure tests**

Create named tests `test_redaction_is_recursive_and_case_insensitive()`,
`test_redaction_pointer_navigates_lists()`,
`test_redaction_missing_pointer_is_a_noop()`,
`test_capture_writer_uses_private_permissions()`,
`test_capture_writer_cleans_temp_after_replace_failure()`,
`test_capture_writer_retries_random_name_collision()`,
`test_capture_writer_rejects_max_bytes_before_destination_open()`,
`test_capture_writer_surfaces_disk_error_without_partial_file()`, and
`test_capture_writer_rejects_symlink_component_and_target()`. The main success
test and exact permission assertions are:

```python
def test_capture_writer_redacts_and_round_trips(tmp_path: Path) -> None:
    writer = FailureCaptureWriter(
        FailureCaptureConfig(
            directory=tmp_path / "captures",
            redact_paths=("/body/customer/card",),
        )
    )
    envelope = Envelope(
        body={"password": "p", "customer": {"card": "4111", "amount": Decimal("1.20")}},
        meta={"Authorization": "Bearer secret", "trace": "t-1"},
        attempts=2,
    )

    path = asyncio.run(
        writer.write(
            app="billing",
            task="sync",
            stage="handler",
            terminal=True,
            failure=FailureInfo(FailureKind.ERROR, "ValueError", "bad row"),
            envelope=envelope,
        )
    )
    capture = load_capture(path)
    assert capture.envelope.body == {
        "password": "<redacted>",
        "customer": {"card": "<redacted>", "amount": Decimal("1.20")},
    }
    assert capture.envelope.meta == {"Authorization": "<redacted>", "trace": "t-1"}
    assert set(capture.redacted_paths) == {
        "/body/password",
        "/body/customer/card",
        "/meta/Authorization",
    }
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o077 == 0
```

Use `load_capture(path, expected_app="billing", expected_task="sync")` in the
success test, then assert `expected_app="other"` and `expected_task="other"`
each raise `ValueError` containing both expected and captured identities.

- [ ] **Step 2: Run writer tests and confirm missing symbols**

```bash
uv run pytest tests/test_failure_capture.py -k 'capture_writer or load_capture' -v
```

Expected: imports fail because the writer module is absent.

- [ ] **Step 3: Implement the versioned capture model and redaction**

Create `src/onestep/capture/writer.py` with frozen `LoadedCapture`, constants
`CAPTURE_SCHEMA = "onestep/envelope-capture"` and `CAPTURE_VERSION = 1`, and:

```python
_REDACTED = "<redacted>"
_SECRET_KEYS = {
    "password", "passwd", "secret", "token", "authorization", "cookie",
    "api_key", "apikey", "dsn", "database_url", "connection_string",
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


def redact_envelope(envelope: Envelope, pointers: tuple[str, ...]) -> tuple[Envelope, tuple[str, ...]]:
    logical = {"body": copy.deepcopy(envelope.body), "meta": copy.deepcopy(envelope.meta)}
    redacted: set[str] = set()
    logical = _redact_secret_keys(logical, path="", redacted=redacted)
    for pointer in pointers:
        if _replace_pointer(logical, pointer, _REDACTED):
            redacted.add(pointer)
    return (
        Envelope(body=logical["body"], meta=logical["meta"], attempts=envelope.attempts),
        tuple(sorted(redacted)),
    )
```

Normalize secret keys with `key.casefold().replace("-", "_")`. Decode JSON
Pointer `~0` and `~1`, accept dictionary keys and integer list/tuple indices, and
treat missing paths as no-ops. Preserve tuple/namedtuple/set container types
while recursively redacting built-in keys.

- [ ] **Step 4: Implement secure atomic writing**

Implement `FailureCaptureWriter.write()` as an async `asyncio.to_thread()`
wrapper around `_write_sync()`. `_write_sync()` must:

1. reject any existing symlink component with `os.lstat()`;
2. create the directory with mode `0700`, then `chmod(0700)` only when newly created;
3. redact before encoding;
4. encode `body` and `meta` with `encode_value()`;
5. serialize the capture document with
   `json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2)`;
6. reject UTF-8 output larger than `max_bytes` before opening a destination;
7. create a random same-directory temporary file with
   `flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)`
   and `os.open(temp_path, flags, 0o600)`; when `O_NOFOLLOW` is unavailable,
   immediately verify `os.fstat(fd)` is a regular file and verify the path with
   `os.lstat(temp_path)` refers to the same `(st_dev, st_ino)` before writing;
8. flush and `fsync()`, reject an existing/symlink final path, then `os.replace()`;
9. unlink the temporary file in `finally`.

Use filenames `<UTC YYYYmmddTHHMMSSffffffZ>-<uuid4 hex>.json`; never derive a
name from app, task, payload, or exception data.

- [ ] **Step 5: Implement strict replay loading**

`load_capture()` must parse JSON, require exact schema/version, decode body and
meta, require non-negative integer attempts, validate all required scalar
fields, and reject app/task mismatches with messages containing expected and
received identities. It must return `LoadedCapture`, never an `Envelope`
subclass.

- [ ] **Step 6: Run capture persistence tests**

```bash
uv run pytest tests/test_failure_capture.py -k 'capture_writer or redacts or load_capture or symlink or atomic' -v
```

The persisted document has exactly the design's keys: schema, version,
UTC `captured_at`, app, task, stage, terminal, failure, envelope, and
redacted_paths. Persist only failure `kind`, `exception_type`, and `message`;
do not persist `FailureInfo.traceback`. The envelope object contains encoded
body/meta and the original non-negative attempts.

Expected: all selected tests pass.

- [ ] **Step 7: Commit capture persistence**

```bash
git add src/onestep/capture tests/test_failure_capture.py
git commit -m "feat: persist redacted failure captures"
```

## Task 5: Extract The Production Delivery Executor

**Files:**
- Create: `src/onestep/runtime/executor.py`
- Modify: `src/onestep/runtime/runner.py`
- Test: `tests/contract/test_runtime_contract.py`

- [ ] **Step 1: Add a delegation contract test**

Monkeypatch `onestep.runtime.runner.DeliveryExecutor` with a fake whose
`execute()` records the delivery. Assert `TaskRunner._handle_delivery()` calls it
once and still returns `None`. Keep the production characterization tests from
Task 1 unchanged.

- [ ] **Step 2: Run the delegation test and confirm the missing executor**

```bash
uv run pytest tests/contract/test_runtime_contract.py -k delivery_executor_delegation -v
```

Expected: fail because `DeliveryExecutor` is not imported by `runner.py`.

- [ ] **Step 3: Create executor result and collaborator types**

Create `src/onestep/runtime/executor.py` with these internal types:

```python
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


EventEmitter = Callable[[TaskEvent], Awaitable[None]]
SinkDispatcher = Callable[[Sink, Envelope, str], Awaitable[bool]]
Checkpoint = Callable[[str, str, Mapping[str, Any]], Awaitable[None]]
```

`SinkDispatcher` returns `True` when a real send occurred and `False` when a
diagnostic policy suppressed it; it raises on send failure. Use a no-op async
checkpoint by default.

When `_handle_failure()` receives the original exception, set
`outcome.public_failure` with only `exception_type` and `failure_kind`. For
`ConnectorOperationError`, add its normalized `backend`, `operation`, and
`connector_kind`; never copy `str(exc)`, traceback, payload, DSN, or credentials
into this public diagnostic field.

- [ ] **Step 4: Move single-delivery behavior without semantic edits**

Implement:

```python
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
        self.emit_event = emit_event or app.emit_event
        self.dispatch_sink = dispatch_sink or self._dispatch_production_sink
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
            await self._emit(TaskEventKind.STARTED, delivery)
            active_stage = "before_hook"
            await self.checkpoint("before_hook", "entered", {})
            await self._run_hooks(self.task.hooks.before, ctx, delivery.payload)
            await self.checkpoint("before_hook", "completed", {})
            active_stage = "handler"
            await self.checkpoint("handler", "entered", {})
            outcome.handler_result = await self._invoke_handler(ctx, delivery)
            await self.checkpoint("handler", "completed", {})
            active_stage = "after_success_hook"
            await self.checkpoint("after_success_hook", "entered", {})
            await self._run_hooks(self.task.hooks.after_success, ctx, delivery.payload, outcome.handler_result)
            await self.checkpoint("after_success_hook", "completed", {})
            if outcome.handler_result is not None and self.task.emit_routes:
                active_stage = "route"
                await self.checkpoint("route", "entered", {})
                selected = await self._select_emit_sinks(ctx, delivery.payload, outcome.handler_result)
                outcome.selected_sinks = [getattr(sink, "name", type(sink).__name__) for sink in selected]
                await self.checkpoint("route", "completed", {"selected_sinks": outcome.selected_sinks})
                emitted = Envelope(body=outcome.handler_result)
                for sink in selected:
                    active_stage = "sink"
                    await self.dispatch_sink(sink, emitted, "emit")
            active_stage = "ack"
            outcome.delivery_action = DeliveryAction.ACK
            if self.apply_delivery_actions:
                await delivery.ack()
            await self._emit(
                TaskEventKind.SUCCEEDED,
                delivery,
                duration_s=time.perf_counter() - started_at,
                event_meta=self._build_succeeded_event_meta(delivery, outcome.handler_result),
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
            return await self._handle_failure(ctx, delivery, exc, FailureKind.TIMEOUT, outcome, started_at)
        except Exception as exc:
            outcome.failure_stage = active_stage
            return await self._handle_failure(ctx, delivery, exc, FailureKind.ERROR, outcome, started_at)
```

Move these methods with the exact existing positional/keyword parameters and
return contracts, changing only their owner and collaborator calls:

The exact signatures are `_invoke_handler(self, ctx: TaskContext, delivery:
Delivery) -> Any`, `_select_emit_sinks(self, ctx: TaskContext, payload: Any,
result: Any) -> tuple[Sink, ...]`, `_handle_failure(self, ctx: TaskContext,
delivery: Delivery, exc: Exception, kind: FailureKind, outcome:
ExecutionOutcome, started_at: float) -> ExecutionOutcome`,
`_publish_dead_letter(self, ctx: TaskContext, delivery: Delivery, failure:
FailureInfo, *, duration_s: float | None) -> bool`, `_fail_delivery(self, ctx:
TaskContext, delivery: Delivery, exc: Exception) -> bool`, and the current
`_run_task_hooks()` / `_emit_event()` signatures renamed to `_run_hooks()` /
`_emit()` without changing their parameter defaults.

Also move `_send_to_sink()`, `_build_succeeded_event_meta()`,
`_extract_handler_notifications()`, and `_sanitize_notification_value()` with
their current signatures and statements. `_dispatch_production_sink()` must
call the moved `_send_to_sink()` and return `True`. Replace direct sink calls in
dead-letter publication with `dispatch_sink(sink, envelope, "dead_letter")`.
Treat a `False` dispatch return as a deliberately suppressed diagnostic send,
not as a publish failure: leave `dead_letter_attempted=False` and
`dead_letter_published=None`, but continue to the predicted terminal source
action. Only a raised dispatch exception triggers the existing dead-letter
fallback-to-retry path. A `True` return sets `dead_letter_attempted=True` and
contributes to `dead_letter_published=True` after every configured sink succeeds.
When `apply_delivery_actions=False`, set the outcome action/delay exactly as
production resolution dictates but do not call `delivery.ack()`, `retry()`, or
`fail()`. In this mode `_fail_delivery()` returns `True` because the predicted
source fail action itself cannot be observed; a real dead-letter send failure
still produces `would_retry` through the unchanged publication fallback.
Before failure hooks, dead-letter publication, and delivery actions, update
`active_stage` through a small `_set_stage()` helper and emit matching
`entered`/`completed` checkpoints. `_handle_failure()` returns the populated
`ExecutionOutcome` after the unchanged retry/dead-letter/fail event sequence.

- [ ] **Step 5: Make TaskRunner a compatibility facade**

Instantiate one executor in `TaskRunner.__init__` and replace the old body:

```python
self._executor = DeliveryExecutor(app, task)

async def _handle_delivery(self, delivery: "Delivery") -> None:
    await self._executor.execute(delivery)
```

Leave fetching, inflight tracking, pause/drain/shutdown handling, and public
`TaskRunner` imports in `runner.py`. Do not export `DeliveryExecutor` from
`onestep.runtime` or top-level `onestep`.

- [ ] **Step 6: Run all production contract tests**

```bash
uv run pytest tests/contract/test_runtime_contract.py -v
```

Expected: all tests pass unchanged, including Task 1 ordering and existing
`run_task_once()` behavior.

- [ ] **Step 7: Commit executor extraction**

```bash
git add src/onestep/runtime tests/contract/test_runtime_contract.py
git commit -m "refactor: extract delivery executor"
```

## Task 6: Integrate Production Failure Capture At Final Action Resolution

**Files:**
- Modify: `src/onestep/runtime/executor.py`
- Modify: `src/onestep/app.py`
- Test: `tests/contract/test_runtime_contract.py`
- Test: `tests/test_failure_capture.py`

- [ ] **Step 1: Write failing terminal/all capture tests**

Create tests using temporary capture directories for these exact outcomes:

- `terminal` writes after successful dead-letter publication and successful `delivery.fail()`;
- `terminal` does not write for retry policy decisions;
- `terminal` does not write when dead-letter send fails and original delivery is retried;
- `terminal` does not write when `delivery.fail()` fails and falls back to retry;
- `all` writes non-terminal retry attempts with `terminal: false`;
- unsupported payload logs one ERROR containing app/task/stage/type/path, writes no file, and preserves the original retry/fail action.

Use existing delivery/sink test doubles and assert task event sequences remain
identical to Task 1.

- [ ] **Step 2: Run focused tests and confirm no files are produced**

```bash
uv run pytest tests/contract/test_runtime_contract.py tests/test_failure_capture.py -k 'failure_capture_runtime' -v
```

Expected: fail because the executor does not call the writer.

- [ ] **Step 3: Create one writer per configured app**

In `OneStepApp.__init__` add:

```python
self._failure_capture_writer = FailureCaptureWriter(failure_capture) if failure_capture is not None else None
```

Keep this object internal and do not treat it as a resource: startup/shutdown
must not open or close it.

- [ ] **Step 4: Capture only after the effective action is known**

Make `_fail_delivery()` return `True` only when `delivery.fail()` succeeds and
`False` after fallback retry. Set `ExecutionOutcome.terminal` from the effective
result, not merely from `RetryDecision.FAIL`. At the end of every handled
failure call:

```python
async def _capture_failure(self, delivery: Delivery, outcome: ExecutionOutcome) -> None:
    writer = self.app._failure_capture_writer
    if writer is None or outcome.failure is None:
        return
    if self.app.failure_capture.mode == "terminal" and not outcome.terminal:
        return
    try:
        await writer.write(
            app=self.app.name,
            task=self.task.name,
            stage=outcome.failure_stage or "delivery_action",
            terminal=outcome.terminal,
            failure=outcome.failure,
            envelope=delivery.envelope,
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
            extra={"app_name": self.app.name, "task_name": self.task.name},
        )
```

Call it after retry/dead-letter/fail resolution and before returning from the
executor. Never re-raise capture failures.

- [ ] **Step 5: Run capture and production contracts**

```bash
uv run pytest tests/test_failure_capture.py tests/contract/test_runtime_contract.py -k 'failure_capture_runtime or single_delivery_success_order or dead_letter or fail_action' -v
```

Expected: all selected tests pass; capture failures do not alter actions.

- [ ] **Step 6: Commit runtime capture integration**

```bash
git add src/onestep/app.py src/onestep/runtime/executor.py tests/test_failure_capture.py tests/contract/test_runtime_contract.py
git commit -m "feat: capture runtime delivery failures"
```

## Task 7: Build The In-Process Diagnostic Runner

**Files:**
- Create: `src/onestep/diagnostics/__init__.py`
- Create: `src/onestep/diagnostics/models.py`
- Create: `src/onestep/diagnostics/runner.py`
- Test: `tests/test_diagnostics.py`

- [ ] **Step 1: Write diagnostic model and dry-run tests**

Create `tests/test_diagnostics.py`. Use a `RecordingSink` whose `open()`,
`send()`, and `close()` append to a list, and assert the default mode never
touches it:

```python
def test_diagnostic_dry_run_executes_handler_hooks_and_routes_without_sink_io() -> None:
    async def scenario() -> None:
        calls: list[str] = []
        app = OneStepApp("diagnostic")
        sink = RecordingSink("selected", calls)

        async def before(ctx, payload):
            calls.append("before")

        async def after(ctx, payload, result):
            calls.append("after")

        async def predicate(ctx, payload, result):
            calls.append("route")
            return True

        @app.task(
            emit=EmitRoute(predicate=predicate, then_sinks=(sink,)),
            hooks=TaskHooks(before=(before,), after_success=(after,)),
        )
        async def consume(ctx, payload):
            calls.append("handler")
            return {"output": payload["input"]}

        report = await DiagnosticRunner(app).run(
            task_name="consume",
            envelope=Envelope(body={"input": 1}),
            send=False,
        )

        assert calls == ["before", "handler", "after", "route"]
        assert report.completion == "succeeded"
        assert report.mode == "dry-run"
        assert report.selected_sinks == ("selected",)
        assert report.delivery_action == "would_ack"
        assert report.delivery_action_basis == "predicted"
        assert report.side_effect_outcome == "not_attempted"

    asyncio.run(scenario())
```

Add named tests with these exact assertions:

```python
assert [event.kind for event in report.events] == [TaskEventKind.STARTED, TaskEventKind.SUCCEEDED]
assert reporter.events == []
assert report.warning == "handler and task hooks may perform external side effects"
assert report.to_dict()["schema"] == "onestep/diagnostic-result"
assert report.to_dict()["version"] == 1
```

The reporter test attaches a recording reporter/event handler before running;
only the runner's local event list may change.

- [ ] **Step 2: Write send, cleanup, and one-attempt failure tests**

Use two selected `RecordingSink` instances and require:

```python
assert calls == [
    "first:open", "first:send",
    "second:open", "second:send",
    "second:close", "first:close",
]
```

Each selected sink is opened immediately before its send and all opened sinks
are closed in reverse order after execution. Add
`test_diagnostic_send_closes_after_send_failure()` and assert `close` still
follows the failing `send`. Add parameterized retry-policy cases whose body
constructs an app with a handler that always raises, runs one diagnostic, and
asserts the three values shown:

```python
@pytest.mark.parametrize(
    "retry,dead_letter,expected",
    [
        (MaxAttempts(max_attempts=3, delay_s=30), None, "would_retry"),
        (NoRetry(), RecordingSink("dead", []), "would_dead_letter"),
        (NoRetry(), None, "would_fail"),
    ],
)
def test_diagnostic_runs_exactly_one_failed_attempt(retry, dead_letter, expected) -> None:
    async def scenario() -> None:
        calls = 0
        app = OneStepApp("failed-diagnostic")

        @app.task(retry=retry, dead_letter=dead_letter)
        async def consume(ctx, payload):
            nonlocal calls
            calls += 1
            raise ValueError("boom")

        report = await DiagnosticRunner(app).run(
            task_name="consume",
            envelope=Envelope(body={"id": 1}, attempts=0),
            send=False,
        )
        assert calls == 1
        assert report.delivery_action == expected
        assert report.delivery_action_basis == "predicted"

    asyncio.run(scenario())
```

For every row assert the handler call count is one, no backoff sleep occurs,
and `delivery_action_basis == "predicted"`. For dry-run dead-letter assert
`dead_letter == {"attempted": False, "published": None}`. For send mode assert
successful publication gives `{ "attempted": True, "published": True }`, while
a broken dead-letter sink gives `{ "attempted": True, "published": False }`
and `delivery_action == "would_retry"`.

- [ ] **Step 3: Run the diagnostic tests and confirm missing modules**

```bash
uv run pytest tests/test_diagnostics.py -k 'diagnostic_dry_run or diagnostic_send or one_failed_attempt' -v
```

Expected: collection fails because `onestep.diagnostics` does not exist.

- [ ] **Step 4: Implement diagnostic request/result models**

Create `src/onestep/diagnostics/models.py` with this private surface:

```python
DIAGNOSTIC_SCHEMA = "onestep/diagnostic-result"
DIAGNOSTIC_VERSION = 1
SIDE_EFFECT_WARNING = "handler and task hooks may perform external side effects"


@dataclass(frozen=True)
class DiagnosticRequest:
    operation: Literal["run", "replay"]
    target: str
    task: str
    envelope: Envelope | None = None
    capture_path: str | None = None
    send: bool = False


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
            "warning": self.warning,
            "failure": self.failure,
            "failure_stage": self.failure_stage,
            "cleanup": self.cleanup,
            "side_effect_outcome": self.side_effect_outcome,
            "last_checkpoint": self.last_checkpoint,
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
            warning=value["warning"],
            failure=value["failure"],
            failure_stage=value["failure_stage"],
            cleanup=value["cleanup"],
            side_effect_outcome=value["side_effect_outcome"],
            last_checkpoint=value["last_checkpoint"],
        )
```

`_event_to_dict()` and `_event_from_dict()` serialize only existing `TaskEvent`
fields. They must not serialize `FailureInfo.message` or traceback: emit failure
kind and exception type only. The report-level `failure` comes from
`ExecutionOutcome.public_failure`, so official connector failures additionally
carry normalized backend/operation/kind fields while unknown exceptions expose
only their type. `_validate_diagnostic_result()` requires the exact schema/version,
required keys, scalar/container types, allowed completion values, and
non-negative attempts/duration. Do not add fields to `TaskEvent`, `Envelope`,
reporter payloads, or top-level exports.

- [ ] **Step 5: Implement local delivery and sink policies**

Create `src/onestep/diagnostics/runner.py` with:

```python
class _DiagnosticDelivery(Delivery):
    def __init__(self, envelope: Envelope) -> None:
        super().__init__(envelope)
        self.actions: list[tuple[str, float | None]] = []

    async def ack(self) -> None:
        self.actions.append(("ack", None))

    async def retry(self, *, delay_s: float | None = None) -> None:
        self.actions.append(("retry", delay_s))

    async def fail(self, exc: Exception | None = None) -> None:
        self.actions.append(("fail", None))


class DiagnosticRunner:
    def __init__(self, app: OneStepApp, *, checkpoint: Checkpoint | None = None) -> None:
        self.app = app
        self.checkpoint = checkpoint or _noop_checkpoint

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
        opened: list[Sink] = []

        async def emit_event(event: TaskEvent) -> None:
            events.append(event)

        executor = DeliveryExecutor(
            self.app,
            task,
            emit_event=emit_event,
            dispatch_sink=self._sink_dispatcher(send=send, opened=opened),
            apply_delivery_actions=False,
            checkpoint=self.checkpoint,
        )
        started_at = time.perf_counter()
        cleanup_errors: list[Exception] = []
        try:
            outcome = await executor.execute(delivery)
        finally:
            for sink in reversed(opened):
                sink_name = getattr(sink, "name", type(sink).__name__)
                await self.checkpoint("cleanup", "entered", {"resource": sink_name})
                try:
                    await sink.close()
                except Exception as exc:
                    cleanup_errors.append(exc)
                await self.checkpoint("cleanup", "completed", {"resource": sink_name})
        return self._build_report(
            operation=operation,
            send=send,
            envelope=envelope,
            events=events,
            outcome=outcome,
            cleanup_errors=cleanup_errors,
            duration_s=time.perf_counter() - started_at,
        )
```

Resolve exactly one task by name and raise `ValueError` for missing or duplicate
matches. Pass a local `emit_event` collector, `apply_delivery_actions=False`,
the supplied checkpoint, and `_dispatch_sink()` into `DeliveryExecutor`.
Dry-run dispatch calls `encode_value(envelope.body)` and
`encode_value(envelope.meta)` so unrepresentable output fails explicitly, then
returns `False` without `open()`, `send()`, or `close()`.

Send dispatch must use this open/send shape for every individual sink:

```python
await self.checkpoint("sink", "entered", {"resource": sink_name})
if id(sink) not in opened_ids:
    await sink.open()
    opened_ids.add(id(sink))
    opened.append(sink)
await sink.send(envelope)
await self.checkpoint("sink", "completed", {"resource": sink_name})
return True
```

`_sink_dispatcher()` creates the `opened_ids: set[int]` closure used above.

The real dispatcher opens a sink only once by identity, immediately before its
first send, appends it to `opened`, sends, and emits `sink entered/completed`
checkpoints. `DiagnosticRunner.run()` wraps `executor.execute()` in `try/finally`
and closes `reversed(opened)`, emitting `cleanup entered/completed` for each.
Track close errors separately so they set `completion="failed"` and
`cleanup="failed"` without hiding the send result. Never call `app.startup()`,
`app.shutdown()`, app hooks, source methods, reporter attach/start/flush, or
`TaskRunner.run()`.

- [ ] **Step 6: Map execution outcomes into reports**

Map `DeliveryAction.ACK`, `RETRY`, `DEAD_LETTER`, and `FAIL` to `would_ack`,
`would_retry`, `would_dead_letter`, and `would_fail`. Always use
`delivery_action_basis="predicted"` because source actions remain synthetic.
Carry the input envelope's `attempts` into retry resolution and the report.
Copy only `outcome.public_failure` into the report's `failure` field.
Set `side_effect_outcome` to `completed` only when every requested real send
completed; use `not_attempted` in dry-run.

- [ ] **Step 7: Run diagnostic runner tests**

```bash
uv run pytest tests/test_diagnostics.py -k 'diagnostic_' -v
```

Expected: all in-process diagnostic tests pass.

- [ ] **Step 8: Commit the diagnostic runner**

```bash
git add src/onestep/diagnostics tests/test_diagnostics.py
git commit -m "feat: add local diagnostic runner"
```

## Task 8: Add Capability-Based Connectivity Checks

**Files:**
- Create: `src/onestep/diagnostics/connectivity.py`
- Modify: `src/onestep/diagnostics/models.py`
- Test: `tests/test_diagnostics.py`

- [ ] **Step 1: Write inventory and non-probeable store tests**

Create a shared resource registered as `resources["queue"]`, a task source, and
an emit sink. Assert one deduplicated result retains all aliases and roles:

```python
assert report.resources[0].aliases == ("queue", "consume.source", "consume.emit[0]")
assert report.resources[0].roles == ("named", "source", "sink")
```

Use a store whose `load()`, `save()`, and `delete()` raise `AssertionError`.
Bind it as app state and assert:

```python
assert store_result.probe_kind == "none"
assert store_result.status == "not_probeable"
assert store.calls == []
assert report.ok is True
assert "no connection was verified" in report.warnings[0]
```

This test is also the regression guard that a SQLAlchemy store's `auto_create`
path cannot be triggered by connectivity checking.

- [ ] **Step 2: Write lifecycle timeout and cleanup tests**

Use resources whose `open()` succeeds, fails, or blocks and whose `close()`
records its call. Require sequential order and a bounded close attempt after
every invoked open, including failed/timed-out opens:

```python
assert calls == [
    "good:open", "good:close",
    "broken:open", "broken:close",
    "slow:open", "slow:close",
]
assert [item.status for item in report.resources] == ["connected", "failed", "failed"]
assert report.ok is False
```

Assert unknown plugin errors expose only `type(exc).__name__`; for
`ConnectorOperationError`, assert the report includes normalized `backend`,
`operation`, and `kind` but excludes the raw exception message.

- [ ] **Step 3: Run tests and confirm missing checker**

```bash
uv run pytest tests/test_diagnostics.py -k connectivity -v
```

Expected: fail because `check_connectivity()` is missing.

- [ ] **Step 4: Implement resource inventory models**

Add frozen `ConnectivityResourceResult` and `ConnectivityReport` dataclasses to
`models.py`. Their `to_dict()` output uses schema
`onestep/connectivity-result`, version `1`, and includes aliases, roles,
`module.qualname` type, probe kind, status, separate open/close outcome objects,
warnings, and `ok`.

Create `src/onestep/diagnostics/connectivity.py` with:

```python
@dataclass
class _InventoryEntry:
    resource: Any
    aliases: list[str]
    roles: list[str]


def inventory_resources(app: OneStepApp) -> tuple[_InventoryEntry, ...]:
    entries: dict[int, _InventoryEntry] = {}

    def add(resource: Any, alias: str, role: str) -> None:
        if resource is None:
            return
        entry = entries.setdefault(id(resource), _InventoryEntry(resource, [], []))
        if alias not in entry.aliases:
            entry.aliases.append(alias)
        if role not in entry.roles:
            entry.roles.append(role)

    for name, resource in app.resources.items():
        add(resource, name, "named")
    add(app.state, "app.state", "state_store")
    for task in app.tasks:
        add(task.source, f"{task.name}.source", "source")
        for index, sink in enumerate(task.sinks):
            add(sink, f"{task.name}.emit[{index}]", "sink")
        for index, sink in enumerate(task.dead_letter_sinks):
            add(sink, f"{task.name}.dead_letter[{index}]", "dead_letter_sink")
    return tuple(entries.values())
```

- [ ] **Step 5: Implement sequential capability probes**

Use this exact entry point:

```python
async def check_connectivity(app: OneStepApp, *, timeout_s: float) -> ConnectivityReport:
    if timeout_s <= 0:
        raise ValueError("connect timeout must be > 0")
    results = []
    for entry in inventory_resources(app):
        results.append(await _probe_entry(entry, timeout_s=timeout_s))
    return ConnectivityReport.from_results(results)
```

`_probe_entry()` checks `callable(getattr(resource, "open", None))` and
`callable(getattr(resource, "close", None))`. Unless both are true, return
`not_probeable` without calling any other method. Wrap open and close separately.
Coroutine functions run on the diagnostic event loop. Invoke synchronous
methods on a dedicated daemon thread and deliver their result through an
`asyncio.Future`; use `asyncio.wait(..., timeout=timeout_s)` without the default
executor so event-loop shutdown cannot wait forever for a blocked method. If a
synchronous method returns an awaitable, await it with the remaining operation
budget:

```python
async def _invoke_lifecycle(
    method: Callable[[], Any],
    *,
    timeout_s: float,
) -> None:
    ...
```

Call `await _invoke_lifecycle(method, timeout_s=timeout_s)` and always attempt
bounded close once open
was invoked. Continue inventory after all failures. Do not call app hooks,
fetch, send, `load`, `save`, or `delete`.

- [ ] **Step 6: Run connectivity tests**

```bash
uv run pytest tests/test_diagnostics.py -k connectivity -v
```

Expected: all connectivity tests pass, including all-resource reporting after a
partial failure.

- [ ] **Step 7: Commit connectivity checking**

```bash
git add src/onestep/diagnostics tests/test_diagnostics.py
git commit -m "feat: check resource connectivity"
```

## Task 9: Supervise Diagnostics With Versioned JSON IPC

**Files:**
- Create: `src/onestep/diagnostics/ipc.py`
- Create: `src/onestep/diagnostics/targets.py`
- Create: `src/onestep/diagnostics/worker.py`
- Create: `src/onestep/diagnostics/supervisor.py`
- Create: `tests/assets/diagnostic_app.py`
- Modify: `src/onestep/cli.py`
- Modify: `tests/test_diagnostics.py`

- [ ] **Step 1: Write frame validation tests**

Require exact schema/version/kind/sequence validation:

```python
def test_ipc_rejects_non_monotonic_and_unknown_frames() -> None:
    validator = FrameValidator(direction="status")
    validator.accept(decode_frame(encode_frame("checkpoint", sequence=1, payload={"phase": "child_start"})))
    with pytest.raises(IPCProtocolError, match="sequence"):
        validator.accept(decode_frame(encode_frame("checkpoint", sequence=1, payload={})))
    with pytest.raises(IPCProtocolError, match="kind"):
        decode_frame(b'{"schema":"onestep/diagnostic-ipc","version":1,"kind":"other","sequence":2,"payload":{}}')
```

Also reject malformed UTF-8, malformed JSON, unknown schema/version, booleans as
sequence numbers, unallowlisted checkpoint fields, and `final` payloads that do
not validate as a complete diagnostic result.

- [ ] **Step 2: Write spawned timeout and stdout-isolation tests**

Create `tests/assets/diagnostic_app.py` with importable apps/tasks selected by
environment variables. Include synchronous infinite-block handlers/hooks,
stdout/stderr writers, cooperative async cancellation, and a sink that blocks
after recording `entered` to a file. Spawned tests use `monkeypatch.chdir()` to
the copied/temp asset directory and target `diagnostic_app:app`, so the fixture
does not require turning `tests/` into a Python package.

Create a temporary YAML target in the spawned-worker test whose `handler.ref`
points to `diagnostic_app:yaml_handler`, then run the same request
once against `tests.assets.diagnostic_app:app` and once against that YAML path.
Assert both produce `completion="succeeded"` and the same selected-sink/action
fields. This is the end-to-end Python/YAML target parity gate.

Use a real `spawn` process and assert:

```python
started = time.monotonic()
report = supervise_diagnostic(request, timeout_s=0.2, grace_s=0.2)
assert time.monotonic() - started < 1.5
assert report.completion == "timed_out"
assert report.cleanup in {"complete", "incomplete"}
assert report.last_checkpoint["phase"] in {"handler", "before_hook"}
```

For JSON isolation, redirect the parent's stdout/stderr and require exactly one
`json.loads(stdout)` document while child stdout and stderr both appear only in
parent stderr. For a forced timeout during send, assert:

```python
assert report.side_effect_outcome == "unknown"
assert "partial" in report.warning
assert "duplicate" in report.warning
```

Add child-exit-without-final, broken pipe, malformed/truncated status frame, and
no-checkpoint cases. The last must synthesize `phase="child_start"`.

- [ ] **Step 3: Run supervision tests and confirm missing IPC**

```bash
uv run pytest tests/test_diagnostics.py -k 'ipc or supervisor or spawned' -v
```

Expected: fail because IPC and supervision modules do not exist.

- [ ] **Step 4: Implement byte-only JSON frames**

Create `src/onestep/diagnostics/ipc.py`:

```python
IPC_SCHEMA = "onestep/diagnostic-ipc"
IPC_VERSION = 1
STATUS_KINDS = frozenset({"checkpoint", "final"})
CONTROL_KINDS = frozenset({"cancel"})


def encode_frame(kind: str, *, sequence: int, payload: Mapping[str, Any]) -> bytes:
    frame = {
        "schema": IPC_SCHEMA,
        "version": IPC_VERSION,
        "kind": kind,
        "sequence": sequence,
        "payload": dict(payload),
    }
    return json.dumps(frame, ensure_ascii=True, separators=(",", ":")).encode("utf-8")


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
    return frame


class FrameValidator:
    def __init__(self, *, direction: Literal["status", "control"]) -> None:
        self.direction = direction
        self.last_sequence = 0

    def accept(self, frame: Mapping[str, Any]) -> dict[str, Any]:
        sequence = frame["sequence"]
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence <= self.last_sequence:
            raise IPCProtocolError("non-monotonic IPC sequence")
        allowed = STATUS_KINDS if self.direction == "status" else CONTROL_KINDS
        if frame["kind"] not in allowed:
            raise IPCProtocolError("invalid IPC kind for direction")
        payload = _validate_payload(frame["kind"], frame["payload"])
        self.last_sequence = sequence
        return payload
```

`decode_frame()` requires a JSON object with the exact five keys above.
`FrameValidator.accept()` requires a positive non-boolean integer sequence
strictly greater than the previous accepted sequence, enforces direction kinds,
and advances sequence only after the payload validates. Checkpoint payloads use
only `phase`, `transition`, `elapsed_s`, `app`, `task`, `resource`,
`selected_sinks`, `completion`, and `cleanup`; transition is `entered` or
`completed`. No payload, exception message, credential, or arbitrary repr may
enter a checkpoint.

- [ ] **Step 5: Implement the spawned child entry point**

Create `targets.py` and move `_ensure_local_import_paths()`,
`_candidate_import_paths()`, `_target_import_root()`, `_find_project_root()`, and
`_path_on_syspath()` from `cli.py` without changing their statements. Add:

```python
def load_diagnostic_target(target: str) -> OneStepApp:
    _ensure_local_import_paths(target)
    if is_yaml_target(target):
        return load_yaml_app(target)
    return OneStepApp.load(target)
```

Both the CLI parent and spawned child must call this shared loader so Python
targets in the current directory, `src/` layouts, project subdirectories, and
YAML-relative handler imports behave identically.

Create `worker.py` with a module-level picklable function:

```python
def diagnostic_worker_main(
    request_bytes: bytes,
    control_rx: Connection,
    status_tx: Connection,
    stderr_handle: Any,
) -> None:
    stderr_fd = stderr_handle.detach()
    os.dup2(stderr_fd, 1)
    os.dup2(stderr_fd, 2)
    asyncio.run(_run_worker(request_bytes, control_rx, status_tx))
```

The request bytes use a separately validated internal request JSON document;
never pickle a request or app object. Add `encode_request()` and
`decode_request()` to `models.py`. A `run` request requires an envelope and
encodes its body/meta with the capture codec; a `replay` request requires a
capture path and must not carry an envelope. Both require operation, target,
task, and send, and run attempts must be non-negative. The worker loads the
target module graph before decoding a run envelope or loading a replay capture
so enum/namedtuple types are already importable.
`_run_worker()` sends `child_start`
entered/completed checkpoints, loads Python or YAML targets in the child,
validates replay identity through `load_capture(capture_path,
expected_app=app.name, expected_task=request.task)`, and invokes
`DiagnosticRunner`. Target, request, capture, and exact task-name validation
occur only in this child. Validation exceptions produce a valid final report
with `completion="validation_failed"`; execution exceptions after validation
produce `child_failed`. A daemon control thread blocks on `control_rx.recv_bytes()`,
validates only `cancel`, and calls `loop.call_soon_threadsafe(task.cancel)`.
Every child status write is
`status_tx.send_bytes(encode_frame(kind, sequence=sequence, payload=payload))`;
never call `Connection.send()` or serialize a Python object through pickle.
Normal cancellation performs cleanup and sends one `final`; forced process
termination is handled only by the parent.

- [ ] **Step 6: Implement total-deadline supervision**

Create `supervisor.py` with:

```python
def supervise_diagnostic(
    request: DiagnosticRequest,
    *,
    timeout_s: float = 60.0,
    grace_s: float = 5.0,
) -> DiagnosticReport:
    if timeout_s <= 0 or grace_s < 0:
        raise ValueError("diagnostic timeouts must be positive")
    return _SpawnSupervisor(request, timeout_s=timeout_s, grace_s=grace_s).run()
```

Use `multiprocessing.get_context("spawn")` and two unidirectional
`ctx.Pipe(duplex=False)` pairs. The monotonic deadline begins immediately after
`process.start()`. Continuously use `status_rx.poll(short_remaining)` followed by
`recv_bytes()` and retain only the highest validated checkpoint. On deadline,
send one validated cancel frame with
`control_tx.send_bytes(encode_frame("cancel", sequence=1, payload={}))` and
drain for at most `grace_s`. If no valid
final arrives, call `terminate()`, `join()`, and, only if still alive where the
platform provides it, `kill()` followed by another `join()`.
If decode/validation of any status frame fails, retain the last previously valid
checkpoint, terminate the child immediately, and synthesize
`completion="child_failed"`; never accept later frames after a protocol error.

Put all process/pipe cleanup in one outer `finally`:

```python
finally:
    for endpoint in (control_tx, control_rx, status_tx, status_rx):
        endpoint.close()
    if process.is_alive():
        process.terminate()
    process.join(timeout=grace_s)
    if process.is_alive() and hasattr(process, "kill"):
        process.kill()
        process.join()
```

Close the parent copies of the child-only `control_rx` and `status_tx`
immediately after a successful `start()` so broken-pipe/EOF detection works.
Track `started = False` before start and guard `is_alive()`/`join()` calls so a
spawn failure also closes every pipe without calling process methods that
require a PID. Pass stderr with the platform multiprocessing reduction handle
(`DupFd` on POSIX, `DupHandle` on Windows); the child detaches the handle before
`dup2()`. Every return path must pass through this `finally`; no child may
outlive the supervisor. Synthesize `timed_out` or `child_failed` from the last valid
checkpoint, defaulting to `child_start`. If a send has an `entered` checkpoint
without a matching `completed`, set `side_effect_outcome="unknown"` and use the
duplicate/partial-write warning. The supervisor returns a model and never
prints; the CLI parent alone renders stdout.

- [ ] **Step 7: Run IPC and supervision tests**

```bash
uv run pytest tests/test_diagnostics.py -k 'ipc or supervisor or spawned' -v
uv run pytest tests/test_cli.py -k 'loads_modules_from or loads_yaml_handlers' -v
```

Expected: all tests pass, and blocking synchronous fixtures return within the
configured deadline plus grace period; existing import-path behavior is
unchanged after moving the helpers.

- [ ] **Step 8: Commit process supervision**

```bash
git add src/onestep/diagnostics src/onestep/cli.py tests/assets/diagnostic_app.py tests/test_diagnostics.py
git commit -m "feat: supervise local diagnostics"
```

## Task 10: Add CLI Commands, Reports, And Exit Codes

**Files:**
- Modify: `src/onestep/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write parser and argv-normalization tests**

Add exact parser assertions:

```python
def test_task_command_bypasses_legacy_run_shorthand() -> None:
    args = parse_args(["task", "run", "pkg.jobs:app", "--task", "sync", "--input", "input.json"])
    assert (args.command, args.task_command, args.target) == ("task", "run", "pkg.jobs:app")


def test_explicit_run_still_accepts_reserved_task_target() -> None:
    assert parse_args(["run", "task"]).target == "task"


def test_other_bare_targets_keep_legacy_shorthand() -> None:
    args = parse_args(["pkg.jobs:app"])
    assert (args.command, args.target) == ("run", "pkg.jobs:app")
```

Assert both task subcommands default `timeout=60.0` and `send=False`, reject
non-positive timeouts, and `check --connect` defaults `connect_timeout=10.0`.

- [ ] **Step 2: Write command and rendering tests**

Monkeypatch `supervise_diagnostic()` and `check_connectivity()` at the CLI
boundary. Require run input to accept any single JSON value, replay to pass the
capture envelope, human output to carry the side-effect warning, and JSON output
to parse as exactly one object. Parameterize exit mapping:

```python
@pytest.mark.parametrize(
    "completion,expected",
    [
        ("succeeded", 0),
        ("failed", 1),
        ("timed_out", 1),
        ("child_failed", 1),
        ("validation_failed", 2),
    ],
)
def test_task_exit_codes(completion, expected) -> None:
    report = make_diagnostic_report(completion=completion)
    assert _diagnostic_exit_code(report) == expected
```

Assert invalid input JSON, unreadable input, bad capture version, app/task
mismatch, target load failure, and argparse validation return `2`. Connectivity
returns `0` for connected/not-probeable-only reports and `1` for any failed
lifecycle probe.

- [ ] **Step 3: Run CLI tests and confirm unknown command/options**

```bash
uv run pytest tests/test_cli.py -k 'task_command or diagnostic or connect' -v
```

Expected: parser tests fail because `task` and `--connect` are unknown.

- [ ] **Step 4: Add nested parsers and reserve `task`**

Add `task_parser = subparsers.add_parser("task", help="Run local task diagnostics")`, required nested
subparsers `run` and `replay`, and shared arguments:

```python
def _positive_seconds(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive number of seconds")
    return parsed
```

Run requires `<target> --task <name> --input <path>`; replay requires
`<target> --task <name> --envelope <path>`. Both accept `--send`,
`--timeout` using `_positive_seconds` with default `60.0`, and `--json` stored as
`as_json`. Add `--connect`, `--connect-timeout` with default `10.0`, and existing
`--json` support to `check`.

Change `_normalize_argv()` exactly to:

```python
if argv[0].startswith("-") or argv[0] in {
    "run", "check", "init", "build", "catalog", "task"
}:
    return argv
```

- [ ] **Step 5: Dispatch diagnostics in the parent CLI**

Before loading an app in `main()`, branch `args.command == "task"`. Read input
JSON in the parent only to validate it, construct a versioned request for the
child, call `supervise_diagnostic()`, then render the returned report. Replay
sends `capture_path` rather than a decoded envelope and performs target loading,
capture decoding, identity checks, and task validation only in the supervised
child. This keeps import side effects single-shot and inside the overall
deadline. Print the
side-effect warning to stderr for both dry-run and send modes; JSON stdout must
contain only `json.dumps(report.to_dict(), indent=2)`.

For `check --connect`, keep existing target loading and strict YAML behavior,
run `asyncio.run(check_connectivity(app, timeout_s=args.connect_timeout))`, and
render a connectivity report instead of the ordinary summary. Without
`--connect`, preserve the current summary byte-for-byte.
Import the moved path helpers from `diagnostics.targets` for all CLI commands so
existing local/source-layout target behavior remains covered by the unchanged
CLI tests.

- [ ] **Step 6: Implement stable renderers and exit mapping**

Use one `_diagnostic_exit_code()` and one `_connectivity_exit_code()`:

```python
def _diagnostic_exit_code(report: DiagnosticReport) -> int:
    if report.completion == "succeeded":
        return 0
    if report.completion == "validation_failed":
        return 2
    return 1


def _connectivity_exit_code(report: ConnectivityReport) -> int:
    return 0 if report.ok else 1
```

Parent-side input/argument validation exceptions return `2`. Child-side target,
capture, identity, and task validation returns a complete
`validation_failed` report, which this helper maps to `2`; child validation
diagnostics remain on stderr. Human diagnostic output includes operation, target,
app/task, mode, completion, failure stage, selected sinks, predicted delivery
action, dead-letter attempted/published, cleanup, side-effect outcome, last
checkpoint, duration, and warning. Human connectivity output prints every
resource and separate open/close outcomes.

- [ ] **Step 7: Run all CLI tests**

```bash
uv run pytest tests/test_cli.py -v
```

Expected: all existing and new CLI tests pass; prior `run`, `check`, `init`,
`build`, and `catalog` behavior remains unchanged.

- [ ] **Step 8: Commit the CLI surface**

```bash
git add src/onestep/cli.py tests/test_cli.py
git commit -m "feat: add local task diagnostics CLI"
```

## Task 11: Document P2 And Prepare Core 1.8.0 Metadata

**Files:**
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `docs/yaml-task-definition.md`
- Modify: `docs/framework-evolution-roadmap.md`
- Modify: `CHANGELOG.md`
- Modify: `pyproject.toml`
- Modify: `uv.lock`

- [ ] **Step 1: Update English and Chinese user documentation**

Add matching local diagnostics sections containing the exact three command
forms from the design. State all of these constraints in both languages:

- dry-run suppresses only framework-managed sinks; handlers/hooks can still
  write externally;
- `would_dead_letter` in dry-run is conditional on successful production
  dead-letter publication and source finalization;
- `--send` observes sink publication but never a real source action;
- `--timeout` defaults to 60 seconds and forced termination during `--send` can
  leave partial or duplicate writes with an unknown outcome;
- connectivity probes only callable `open()`/`close()` pairs and reports stores
  without lifecycle as `not_probeable` without calling `load()`;
- `mode=all` is best-effort; unsupported custom values log safe type/path
  context and produce no lossy capture;
- captures can retain PII and require access and retention controls.

- [ ] **Step 2: Document YAML capture policy and roadmap status**

Add the complete `app.failure_capture` YAML example to
`docs/yaml-task-definition.md`, including defaults and supported lossless types.
In `docs/framework-evolution-roadmap.md`, mark P2 complete only in the final
implementation commit after Task 12 gates have passed; preserve P3 as the next
phase and do not add P3 protocol fields. Align the P2 deliverable row with the
approved design by removing the stale `handler scaffold tests` phrase; handler
scaffolding is not in the approved P2 design or success criteria. Update the
committed command examples to the final argument order and include `--send`,
`--timeout`, and `--connect-timeout` options.

- [ ] **Step 3: Update changelog and version metadata**

Add a `1.8.0` changelog entry covering commands, capture policy, overall
timeout/IPC behavior, compatibility, reserved bare shorthand target `task`, and
the `--send` side-effect warning. Change only the core version:

```toml
[project]
name = "onestep"
version = "1.8.0"
```

Run:

```bash
uv lock
```

Expected: `uv.lock` changes the root `onestep` package version to `1.8.0` and
does not bump plugin versions or add upper bounds.

- [ ] **Step 4: Verify documentation and lock consistency**

```bash
rg -n 'task run|task replay|--connect|not_probeable|mode=all|unknown|1\.8\.0' README.md README.zh-CN.md docs/yaml-task-definition.md docs/framework-evolution-roadmap.md CHANGELOG.md pyproject.toml uv.lock
uv lock --check
```

Expected: every required concept is present and the lockfile is current.

- [ ] **Step 5: Commit documentation and release metadata**

```bash
git add README.md README.zh-CN.md docs/yaml-task-definition.md docs/framework-evolution-roadmap.md CHANGELOG.md pyproject.toml uv.lock
git commit -m "docs: document P2 local handler loop"
```

Do not tag, publish, push, or rebuild the Control Plane in this implementation
plan. Those actions require a separate explicit release request. No Control
Plane source or image is changed by P2.

## Task 12: Run Compatibility And Reliability Gates

**Files:**
- Modify only files implicated by a failing gate; keep fixes inside P2 scope.

- [ ] **Step 1: Run focused feature tests**

```bash
uv run pytest -q tests/test_cli.py tests/test_diagnostics.py tests/test_failure_capture.py
```

Expected: PASS.

- [ ] **Step 2: Run production contract tests**

```bash
uv run pytest -q tests/contract/test_runtime_contract.py
```

Expected: PASS with unchanged reporter, WebSocket, remote manual-run,
`Envelope`, and `TaskEvent` contracts.

- [ ] **Step 3: Run all non-integration tests**

```bash
uv run pytest -q -m "not integration"
```

Expected: PASS.

- [ ] **Step 4: Run repository reliability checks**

```bash
./scripts/run-reliability-checks.sh
```

Expected: every configured reliability check passes.

- [ ] **Step 5: Inspect the final diff and compatibility boundary**

```bash
git diff --check
git diff --stat 611cf49..HEAD
git diff 611cf49..HEAD -- src/onestep/events.py src/onestep/envelope.py plugins/onestep-control-plane
```

Expected: no whitespace errors; no changes to `events.py`, `envelope.py`, or
Control Plane code. Confirm `DeliveryExecutor` and all diagnostics modules remain
internal except the intended `FailureCaptureConfig` top-level export.

- [ ] **Step 6: Mark P2 complete after all gates pass**

If Step 1 through Step 5 all passed, update the P2 roadmap status from planned
to complete and amend the Task 11 documentation commit:

```bash
git add docs/framework-evolution-roadmap.md
git commit --amend --no-edit
```

If any gate fails, leave P2 unmarked and fix the scoped failure before repeating
all gates.
