from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Envelope:
    body: Any
    meta: dict[str, Any] = field(default_factory=dict)
    # Zero-based retry history: 0 is the first delivery. Sources document whether
    # counts survive process/ownership changes and whether broker receives count.
    attempts: int = 0
