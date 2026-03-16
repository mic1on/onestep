from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Envelope:
    body: Any
    meta: dict[str, Any] = field(default_factory=dict)
    attempts: int = 0
