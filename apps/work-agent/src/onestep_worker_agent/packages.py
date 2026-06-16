from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path


def extract_package(content: bytes, checksum_sha256: str, target_dir: Path) -> None:
    actual = hashlib.sha256(content).hexdigest()
    if actual != checksum_sha256:
        raise ValueError("package checksum mismatch")

    target_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        for member in archive.infolist():
            target_path = target_dir / member.filename
            if not target_path.resolve().is_relative_to(target_dir.resolve()):
                raise ValueError("package contains unsafe path")
        archive.extractall(target_dir)
