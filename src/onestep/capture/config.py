from __future__ import annotations

from collections.abc import Mapping, Sequence
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
        if isinstance(self.redact_paths, (str, bytes)) or not isinstance(
            self.redact_paths, Sequence
        ):
            raise TypeError("failure_capture.redact_paths must be a sequence")
        paths = tuple(self.redact_paths)
        for path in paths:
            if not isinstance(path, str) or not path.startswith("/"):
                raise ValueError(
                    "failure_capture.redact_paths entries must be JSON Pointer paths"
                )
        object.__setattr__(self, "directory", Path(self.directory))
        object.__setattr__(self, "redact_paths", paths)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "FailureCaptureConfig":
        allowed = {"directory", "mode", "max_bytes", "redact_paths"}
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise ValueError(
                "unsupported fields for app.failure_capture: " + ", ".join(unknown)
            )
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


__all__ = ["CaptureMode", "FailureCaptureConfig"]
