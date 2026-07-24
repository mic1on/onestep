from __future__ import annotations

import hashlib
import io
import zipfile

import pytest

from onestep_worker_agent.packages import extract_package


def build_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("worker.yaml", "app:\n  name: demo\n")
    return buffer.getvalue()


def test_extract_package_verifies_checksum(tmp_path) -> None:
    content = build_zip()
    checksum = hashlib.sha256(content).hexdigest()
    target = tmp_path / "deployment"

    extract_package(content, checksum, target)

    assert (target / "worker.yaml").read_text() == "app:\n  name: demo\n"


def test_extract_package_rejects_checksum_mismatch(tmp_path) -> None:
    with pytest.raises(ValueError, match="package checksum mismatch"):
        extract_package(build_zip(), "0" * 64, tmp_path / "deployment")


def test_extract_package_rejects_unsafe_paths(tmp_path) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("../escape.txt", "bad")
    content = buffer.getvalue()
    checksum = hashlib.sha256(content).hexdigest()

    with pytest.raises(ValueError, match="unsafe path"):
        extract_package(content, checksum, tmp_path / "deployment")
