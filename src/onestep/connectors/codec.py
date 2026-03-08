from __future__ import annotations

import json
from typing import Any

from onestep.envelope import Envelope



def encode_envelope(envelope: Envelope) -> bytes:
    payload = {
        "body": envelope.body,
        "meta": envelope.meta,
        "attempts": envelope.attempts,
    }
    return json.dumps(payload, default=str).encode("utf-8")



def decode_envelope(raw: bytes | str | dict[str, Any]) -> Envelope:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")

    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return Envelope(body=raw)
    else:
        parsed = raw

    if not isinstance(parsed, dict):
        return Envelope(body=parsed)

    if "body" not in parsed:
        return Envelope(body=parsed)

    meta = parsed.get("meta")
    if not isinstance(meta, dict):
        meta = {}
    attempts = parsed.get("attempts")
    if not isinstance(attempts, int):
        attempts = 0
    return Envelope(body=parsed.get("body"), meta=meta, attempts=attempts)
