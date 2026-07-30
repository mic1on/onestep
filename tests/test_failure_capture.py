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

from onestep import FailureCaptureConfig
from onestep.capture.codec import CaptureEncodingError, decode_value, encode_value
from onestep.capture.writer import FailureCaptureWriter, load_capture, redact_envelope
from onestep.config import load_app_config
from onestep.envelope import Envelope
from onestep.retry import FailureInfo, FailureKind


class CaptureStatus(Enum):
    READY = ("ready", 1)


CapturePoint = namedtuple("CapturePoint", "x y")


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
    encoded_local = encode_value(LocalPoint(1))
    with pytest.raises(ValueError, match="unavailable"):
        decode_value(encoded_local)


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
