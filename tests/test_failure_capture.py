from __future__ import annotations

import asyncio
import json
from collections import namedtuple
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from uuid import UUID

import pytest

from onestep import FailureCaptureConfig, MaxAttempts, NoRetry, OneStepApp
from onestep.capture.codec import CaptureEncodingError, decode_value, encode_value
from onestep.capture.writer import FailureCaptureWriter, load_capture, redact_envelope
from onestep.connectors.base import Delivery, Sink
from onestep.config import load_app_config
from onestep.envelope import Envelope
from onestep.retry import FailureInfo, FailureKind
from onestep.runtime import TaskRunner


class CaptureStatus(Enum):
    READY = ("ready", 1)


CapturePoint = namedtuple("CapturePoint", "x y")


class _CaptureDelivery(Delivery):
    def __init__(self, body, *, fail_raises: bool = False) -> None:
        super().__init__(Envelope(body=body))
        self.fail_raises = fail_raises
        self.acked = False
        self.retried = False
        self.failed = False

    async def ack(self) -> None:
        self.acked = True

    async def retry(self, *, delay_s: float | None = None) -> None:
        self.retried = True
        self.envelope = Envelope(
            body=self.envelope.body,
            meta=dict(self.envelope.meta),
            attempts=self.envelope.attempts + 1,
        )

    async def fail(self, exc: Exception | None = None) -> None:
        self.failed = True
        if self.fail_raises:
            raise RuntimeError("fail unavailable")


class _CaptureSink(Sink):
    def __init__(self, *, broken: bool = False) -> None:
        super().__init__("capture-dead")
        self.broken = broken

    async def send(self, envelope: Envelope) -> None:
        if self.broken:
            raise RuntimeError("dead letter unavailable")


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
        ({"max_bytes": True}, "must be an integer"),
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


@pytest.mark.parametrize(
    "capture,match",
    [
        ({"directory": "captures", "unknown": True}, "unsupported fields"),
        ({"mode": "terminal"}, "directory is required"),
        ({"directory": 1}, "path string"),
        ({"directory": "captures", "redact_paths": "password"}, "must be a list"),
    ],
)
def test_yaml_failure_capture_rejects_invalid_schema(capture, match) -> None:
    with pytest.raises((TypeError, ValueError), match=match):
        load_app_config(
            {"app": {"name": "invalid", "failure_capture": capture}, "tasks": []},
            strict=True,
        )


def test_capture_codec_round_trips_supported_values() -> None:
    value = {
        "at": datetime(2026, 7, 30, 9, 15, tzinfo=timezone.utc),
        "id": UUID("d78e3d0d-13e1-44ef-96a5-10bd28fc882d"),
        "raw": b"\x00\xff",
        "amount": Decimal("-0.0100"),
        "decimal_nan": Decimal("NaN"),
        "tuple": (1, "x"),
        "enum": CaptureStatus.READY,
        "point": CapturePoint(2, 3),
        "set": {"b", "a"},
        "frozen": frozenset({2, 1}),
        "$onestep": {"type": "user-data"},
    }
    decoded = decode_value(encode_value(value))
    assert decoded["decimal_nan"].is_nan()
    decoded.pop("decimal_nan")
    value.pop("decimal_nan")
    assert decoded == value


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


@pytest.mark.parametrize(
    "value",
    [
        type("CustomInt", (int,), {})(1),
        type("CustomFloat", (float,), {})(1.5),
        type("CustomStr", (str,), {})("value"),
        type("CustomBytes", (bytes,), {})(b"value"),
        type("CustomList", (list,), {})([1]),
        type("CustomDict", (dict,), {})(value=1),
        type("CustomTuple", (tuple,), {})([1]),
        type("CustomSet", (set,), {})({1}),
        type("CustomFrozenSet", (frozenset,), {})({1}),
        type("CustomDatetime", (datetime,), {})(2026, 7, 30),
        type("CustomDecimal", (Decimal,), {})("1.25"),
        type("CustomUUID", (UUID,), {})("d78e3d0d-13e1-44ef-96a5-10bd28fc882d"),
    ],
)
def test_capture_codec_rejects_lossy_builtin_subclasses(value) -> None:
    with pytest.raises(CaptureEncodingError, match="unsupported value type"):
        encode_value(value)


def test_capture_codec_rejects_invalid_values_and_tags() -> None:
    with pytest.raises(CaptureEncodingError, match="mapping keys must be strings"):
        encode_value({1: "value"})
    with pytest.raises(CaptureEncodingError, match="non-finite float"):
        encode_value(float("nan"))
    with pytest.raises(ValueError, match="unknown extension type"):
        decode_value({"$onestep": {"type": "unknown"}})

    encoded = encode_value(CaptureStatus.READY)
    encoded["$onestep"]["value"] = "changed"
    with pytest.raises(ValueError, match="enum value changed"):
        decode_value(encoded)


def test_capture_codec_rejects_unloaded_or_local_types() -> None:
    encoded = encode_value(CaptureStatus.READY)
    encoded["$onestep"]["module"] = "not_loaded_capture_module"
    with pytest.raises(ValueError, match="not loaded"):
        decode_value(encoded)

    LocalPoint = namedtuple("LocalPoint", "x")
    with pytest.raises(CaptureEncodingError, match="not replayable"):
        encode_value(LocalPoint(1))


def test_redact_envelope_handles_secret_keys_and_pointers() -> None:
    envelope = Envelope(
        body={
            "Password": "secret",
            "rows": [{"card": "4111"}, {"card": "4222"}],
        },
        meta={"API-Key": "token", "trace": "t-1"},
        attempts=3,
    )
    redacted, paths = redact_envelope(
        envelope,
        ("/body/rows/1/card", "/body/not-there"),
    )
    assert redacted.body == {
        "Password": "<redacted>",
        "rows": [{"card": "4111"}, {"card": "<redacted>"}],
    }
    assert redacted.meta == {"API-Key": "<redacted>", "trace": "t-1"}
    assert redacted.attempts == 3
    assert paths == ("/body/Password", "/body/rows/1/card", "/meta/API-Key")
    assert envelope.body["Password"] == "secret"


def _write_capture(writer: FailureCaptureWriter, envelope: Envelope) -> Path:
    return asyncio.run(
        writer.write(
            app="billing",
            task="sync",
            stage="handler",
            terminal=True,
            failure=FailureInfo(FailureKind.ERROR, "ValueError", "bad row"),
            envelope=envelope,
        )
    )


def test_capture_writer_redacts_and_round_trips(tmp_path: Path) -> None:
    writer = FailureCaptureWriter(
        FailureCaptureConfig(
            directory=tmp_path / "captures",
            redact_paths=("/body/customer/card",),
        )
    )
    path = _write_capture(
        writer,
        Envelope(
            body={
                "password": "p",
                "customer": {"card": "4111", "amount": Decimal("1.20")},
            },
            meta={"Authorization": "Bearer secret", "trace": "t-1"},
            attempts=2,
        ),
    )
    capture = load_capture(path, expected_app="billing", expected_task="sync")
    assert capture.envelope.body == {
        "password": "<redacted>",
        "customer": {"card": "<redacted>", "amount": Decimal("1.20")},
    }
    assert capture.envelope.meta == {
        "Authorization": "<redacted>",
        "trace": "t-1",
    }
    assert set(capture.redacted_paths) == {
        "/body/password",
        "/body/customer/card",
        "/meta/Authorization",
    }
    assert capture.envelope.attempts == 2
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o077 == 0
    assert not list(path.parent.glob("*.tmp"))


@pytest.mark.parametrize(
    "value",
    [
        type("WriterCustomList", (list,), {})([1]),
        type("WriterCustomDict", (dict,), {})(value=1),
        type("WriterCustomSet", (set,), {})({1}),
    ],
)
def test_capture_writer_rejects_lossy_container_subclasses(
    tmp_path: Path,
    value,
) -> None:
    directory = tmp_path / "captures"
    writer = FailureCaptureWriter(FailureCaptureConfig(directory=directory))

    with pytest.raises(CaptureEncodingError, match="unsupported value type"):
        _write_capture(writer, Envelope(body=value))

    assert list(directory.iterdir()) == []


def test_capture_writer_rejects_oversized_values_without_file(tmp_path: Path) -> None:
    directory = tmp_path / "captures"
    writer = FailureCaptureWriter(
        FailureCaptureConfig(directory=directory, max_bytes=32)
    )
    with pytest.raises(ValueError, match="max_bytes"):
        _write_capture(writer, Envelope(body={"large": "x" * 100}))
    assert list(directory.iterdir()) == []


def test_capture_writer_rejects_symlink_directory(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / "linked"
    try:
        linked.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("symbolic links unavailable")
    writer = FailureCaptureWriter(FailureCaptureConfig(directory=linked))
    with pytest.raises(ValueError, match="symbolic link"):
        _write_capture(writer, Envelope(body={"id": 1}))


def test_load_capture_rejects_version_and_identity_mismatch(tmp_path: Path) -> None:
    path = _write_capture(
        FailureCaptureWriter(FailureCaptureConfig(directory=tmp_path)),
        Envelope(body={"id": 1}),
    )
    with pytest.raises(ValueError, match="expected 'other'.*received 'billing'"):
        load_capture(path, expected_app="other")
    with pytest.raises(ValueError, match="expected 'other'.*received 'sync'"):
        load_capture(path, expected_task="other")

    document = json.loads(path.read_text(encoding="utf-8"))
    document["version"] = 999
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported capture version 999"):
        load_capture(path)


def test_capture_file_contains_no_traceback(tmp_path: Path) -> None:
    writer = FailureCaptureWriter(FailureCaptureConfig(directory=tmp_path))
    path = asyncio.run(
        writer.write(
            app="billing",
            task="sync",
            stage="handler",
            terminal=True,
            failure=FailureInfo(
                FailureKind.ERROR,
                "ValueError",
                "bad",
                "secret traceback",
            ),
            envelope=Envelope(body={"id": 1}),
        )
    )
    assert "secret traceback" not in path.read_text(encoding="utf-8")


def _run_failed_delivery(
    tmp_path: Path,
    *,
    mode: str,
    retry=None,
    dead_letter: Sink | None = None,
    delivery: _CaptureDelivery | None = None,
) -> _CaptureDelivery:
    app = OneStepApp(
        "runtime-capture",
        failure_capture=FailureCaptureConfig(directory=tmp_path, mode=mode),
    )

    @app.task(retry=retry or NoRetry(), dead_letter=dead_letter)
    async def consume(ctx, payload):
        raise ValueError("boom")

    active_delivery = delivery or _CaptureDelivery({"id": 1})
    asyncio.run(TaskRunner(app, app.tasks[0])._handle_delivery(active_delivery))
    return active_delivery


def test_failure_capture_runtime_terminal_writes_after_effective_fail(
    tmp_path: Path,
) -> None:
    delivery = _run_failed_delivery(
        tmp_path,
        mode="terminal",
        dead_letter=_CaptureSink(),
    )
    paths = list(tmp_path.glob("*.json"))
    assert delivery.failed is True
    assert delivery.retried is False
    assert len(paths) == 1
    capture = load_capture(paths[0])
    assert capture.terminal is True
    assert capture.stage == "handler"


def test_failure_capture_runtime_terminal_skips_retrying_outcomes(
    tmp_path: Path,
) -> None:
    retry_delivery = _run_failed_delivery(
        tmp_path / "retry",
        mode="terminal",
        retry=MaxAttempts(max_attempts=3),
    )
    broken_dead_delivery = _run_failed_delivery(
        tmp_path / "dead",
        mode="terminal",
        dead_letter=_CaptureSink(broken=True),
    )
    fail_fallback_delivery = _run_failed_delivery(
        tmp_path / "fail",
        mode="terminal",
        delivery=_CaptureDelivery({"id": 1}, fail_raises=True),
    )
    assert retry_delivery.retried is True
    assert broken_dead_delivery.retried is True
    assert fail_fallback_delivery.retried is True
    assert list(tmp_path.rglob("*.json")) == []


def test_failure_capture_runtime_all_writes_non_terminal_retry(
    tmp_path: Path,
) -> None:
    delivery = _run_failed_delivery(
        tmp_path,
        mode="all",
        retry=MaxAttempts(max_attempts=3),
    )
    paths = list(tmp_path.glob("*.json"))
    assert delivery.retried is True
    assert len(paths) == 1
    capture = load_capture(paths[0])
    assert capture.terminal is False
    assert capture.envelope.attempts == 0


def test_failure_capture_runtime_encoding_error_preserves_action_and_logs_safe_context(
    tmp_path: Path,
    caplog,
) -> None:
    class Unsupported:
        def __repr__(self) -> str:
            return "secret-repr"

    caplog.set_level("ERROR")
    delivery = _run_failed_delivery(
        tmp_path,
        mode="all",
        retry=MaxAttempts(max_attempts=3),
        delivery=_CaptureDelivery({"value": Unsupported()}),
    )
    assert delivery.retried is True
    assert list(tmp_path.glob("*.json")) == []
    capture_logs = [
        record for record in caplog.records if record.message == "failure capture encoding failed"
    ]
    assert len(capture_logs) == 1
    assert capture_logs[0].app_name == "runtime-capture"
    assert capture_logs[0].task_name == "consume"
    assert capture_logs[0].failure_stage == "handler"
    assert capture_logs[0].capture_path == "/value"
    assert "secret-repr" not in capture_logs[0].getMessage()


def test_disabled_failure_capture_does_not_copy_failed_envelope(tmp_path: Path) -> None:
    class NonCopyable:
        def __deepcopy__(self, memo):
            raise TypeError("must not copy")

    app = OneStepApp("capture-disabled")

    @app.task(retry=NoRetry())
    async def consume(ctx, payload):
        raise ValueError("boom")

    delivery = _CaptureDelivery({"value": NonCopyable()})
    asyncio.run(TaskRunner(app, app.tasks[0])._handle_delivery(delivery))

    assert delivery.failed is True
    assert delivery.retried is False


def test_capture_snapshot_failure_preserves_retry_action(
    tmp_path: Path,
    caplog,
) -> None:
    class NonCopyable:
        def __deepcopy__(self, memo):
            raise TypeError("secret snapshot detail")

    caplog.set_level("ERROR")
    delivery = _run_failed_delivery(
        tmp_path,
        mode="all",
        retry=MaxAttempts(max_attempts=3),
        delivery=_CaptureDelivery({"value": NonCopyable()}),
    )

    assert delivery.retried is True
    assert delivery.failed is False
    assert list(tmp_path.glob("*.json")) == []
    snapshot_logs = [
        record
        for record in caplog.records
        if record.message == "failure capture snapshot failed"
    ]
    assert len(snapshot_logs) == 1
    assert snapshot_logs[0].capture_type == "TypeError"
    assert "secret snapshot detail" not in snapshot_logs[0].getMessage()
