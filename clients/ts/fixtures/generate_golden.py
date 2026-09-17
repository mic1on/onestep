"""Generate golden vectors for the TypeScript ExecutionClient.

This script is the single source of truth for cross-language compatibility.
It imports the *real* production digest/cursor algorithms from the installed
onestep-sql package and emits fixture JSON that the TS test suite asserts
against byte-for-byte.

Why this exists: the TS client must reproduce `submission_digest` and the
keyset pagination cursor exactly. Python's `json.dumps` and JavaScript's
`JSON.stringify` are NOT equivalent (float formatting, ISO datetime format),
so a naive TS implementation silently produces a different digest and breaks
idempotency in production.

Run from the repo root:
    python clients/ts/fixtures/generate_golden.py
"""

from __future__ import annotations

import base64
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

OUT_PATH = Path(__file__).parent / "golden.json"


def python_digest(digest_payload: dict) -> str:
    """Verbatim copy of ExecutionStateMachine._digest (machine.py:409-419).

    Kept as an explicit copy so the fixture generator records the exact
    production algorithm rather than importing a private method.
    """
    return hashlib.sha256(
        json.dumps(
            digest_payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("ascii")
    ).hexdigest()


def python_digest_input(
    *,
    namespace: str,
    task_name: str,
    payload: object,
    metadata: object,
    delay_s: float | None,
    expires_at: datetime | None,
) -> dict:
    """Verbatim copy of the digest_payload construction (machine.py:402-408).

    Note: core's ExecutionRequest.__post_init__ runs `_validate_delay`, which
    coerces delay_s to `float(...)` before the digest is ever computed
    (src/onestep/execution.py:60-67). So an int delay of 5 reaches the digest as
    5.0. We reproduce that normalization here.
    """
    if delay_s is not None:
        delay_s = float(delay_s)
    return {
        "namespace": namespace,
        "task_name": task_name,
        "payload": payload,
        "metadata": metadata,
        "delay_s": delay_s,
        "expires_at": expires_at.isoformat() if expires_at else None,
    }


def python_encode_cursor(created_at: datetime, execution_id: UUID) -> str:
    """Verbatim copy of ExecutionStateMachine._encode_cursor (machine.py:1142-1147).

    Note: keys are NOT sorted here, and separators are tight.
    """
    payload = json.dumps(
        {"v": 1, "created_at": created_at.isoformat(), "id": str(execution_id)},
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


# --- vector definitions ---------------------------------------------------

UTC = timezone.utc
CST = timezone(timedelta(hours=8))

# (label, payload, metadata, delay_s, expires_at)
DIGEST_CASES = [
    ("scalars", {"a": 1, "b": "x"}, {}, None, None),
    ("nested", {"a": 1, "b": "x", "n": {"k": None, "f": 1.5}}, {}, None, None),
    ("empty_payload", {}, {}, None, None),
    ("unicode", {"name": "张三", "emoji": "🎉", "mixed": "a中b"}, {}, None, None),
    ("unicode_escape", {"ctrl": "a\tb\nc", "quote": 'say "hi"', "bs": "a\\b"}, {}, None, None),
    # --- the float cases that break naive JSON.stringify ---
    ("float_1_0", {"v": 1.0}, {}, None, None),
    ("float_3_0", {"v": 3.0}, {}, None, None),
    ("float_neg_0_0", {"v": -0.0}, {}, None, None),
    ("float_frac", {"v": 0.1 + 0.2}, {}, None, None),
    ("float_exp_large", {"v": 1e30}, {}, None, None),
    ("float_exp_small", {"v": 1e-7}, {}, None, None),
    ("float_exp_20", {"v": 1e20}, {}, None, None),
    ("float_exp_21", {"v": 1e21}, {}, None, None),
    ("float_many", {"a": 1.0, "b": 2.5, "c": 1e21, "d": 0.0001, "e": 1e-7}, {}, None, None),
    ("int_vs_float", {"i": 1, "f": 1.0}, {}, None, None),
    ("bool_null", {"t": True, "f": False, "n": None}, {}, None, None),
    ("list", {"xs": [1, 2.0, "three", None, {"k": 1}]}, {}, None, None),
    ("nested_empty", {"a": {}, "b": []}, {}, None, None),
    # --- metadata / delay / expires_at participate in the digest ---
    ("metadata", {"a": 1}, {"trace": "abc", "n": 2}, None, None),
    ("delay", {"a": 1}, {}, 30.0, None),
    ("delay_int", {"a": 1}, {}, 5, None),
    ("expires_utc", {"a": 1}, {}, None, datetime(2026, 9, 17, 10, 7, 0, 123456, tzinfo=UTC)),
    ("expires_utc_round", {"a": 1}, {}, None, datetime(2026, 9, 17, 10, 7, 0, tzinfo=UTC)),
    ("expires_offset", {"a": 1}, {}, None, datetime(2026, 9, 17, 18, 7, 0, 123456, tzinfo=CST)),
    # A payload shaped like a real E2E submission. tests/e2e.test.ts asserts the
    # TS-computed digest against this Python-computed value directly, which is
    # the only way to catch a float-formatting regression that is *consistent*
    # within TS (a TS-to-TS re-submit would agree with itself and still be
    # wrong for the server).
    ("e2e_echo_floats", {"orderId": "A-1001", "qty": 3.0, "ratio": 1.0}, {}, None, None),
    ("combined", {"v": 1.0, "s": "中文"}, {"k": 1.0}, 12.5, datetime(2027, 1, 1, 0, 0, 0, tzinfo=UTC)),
]

CURSOR_CASES = [
    ("utc_micro", datetime(2026, 9, 17, 10, 7, 0, 123456, tzinfo=UTC), "01927f3e-1a2b-7c3d-8e4f-5a6b7c8d9e0f"),
    ("utc_round", datetime(2026, 9, 17, 10, 7, 0, tzinfo=UTC), "00000000-0000-0000-0000-000000000000"),
    ("epoch", datetime(1970, 1, 1, 0, 0, 0, tzinfo=UTC), "11111111-2222-3333-4444-555555555555"),
    ("utc_millis", datetime(2026, 9, 17, 10, 7, 0, 123000, tzinfo=UTC), "22222222-3333-4444-5555-666666666666"),
]


def tag_types(value):
    """Encode a Python value with explicit type tags.

    JSON cannot distinguish 1 from 1.0, but the digest absolutely does. Each
    value is therefore wrapped as {"__t": <type>, "v": <value>} so the TS test
    can reconstruct PyFloat / PyDateTime faithfully instead of guessing from a
    bare JSON number.
    """
    if value is None:
        return {"__t": "none"}
    if isinstance(value, bool):
        return {"__t": "bool", "v": value}
    if isinstance(value, int):
        return {"__t": "int", "v": value}
    if isinstance(value, float):
        return {"__t": "float", "v": repr(value)}
    if isinstance(value, str):
        return {"__t": "str", "v": value}
    if isinstance(value, dict):
        return {"__t": "dict", "v": {k: tag_types(v) for k, v in value.items()}}
    if isinstance(value, (list, tuple)):
        return {"__t": "list", "v": [tag_types(item) for item in value]}
    raise TypeError(f"unsupported fixture value: {type(value)!r}")


def main() -> int:
    digest_vectors = []
    for label, payload, metadata, delay_s, expires_at in DIGEST_CASES:
        di = python_digest_input(
            namespace="demo",
            task_name="orders.sync",
            payload=payload,
            metadata=metadata,
            delay_s=delay_s,
            expires_at=expires_at,
        )
        digest_vectors.append(
            {
                "label": label,
                "namespace": "demo",
                "taskName": "orders.sync",
                "payload": tag_types(payload),
                "metadata": tag_types(metadata),
                # delay_s is a float-or-None in Python's digest payload.
                "delayS": None if delay_s is None else repr(float(delay_s)),
                "expiresAt": expires_at.isoformat() if expires_at else None,
                "expectedDigest": python_digest(di),
            }
        )

    cursor_vectors = []
    for label, created_at, exec_id in CURSOR_CASES:
        cursor = python_encode_cursor(created_at, UUID(exec_id))
        # Round-trip: prove the cursor decodes to what we encoded.
        padded = cursor + "=" * (-len(cursor) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        cursor_vectors.append(
            {
                "label": label,
                "createdAt": created_at.isoformat(),
                "id": exec_id,
                "expectedCursor": cursor,
                "decodedKeys": sorted(decoded.keys()),
            }
        )

    fixture = {
        "_generator": "clients/ts/fixtures/generate_golden.py",
        "_algorithm": "sha256(json.dumps(sort_keys=True, separators=(',',':'), ensure_ascii=True))",
        "digest": digest_vectors,
        "cursor": cursor_vectors,
        # The E2E payload the live interop test submits, with the digest Python
        # computes for it. Asserting TS against THIS value is what catches a
        # float regression, because a TS-only round trip would agree with itself.
        "e2ePayload": {
            "payload": tag_types({"orderId": "A-1001", "qty": 3.0, "ratio": 1.0}),
            "expectedDigest": python_digest(
                python_digest_input(
                    namespace="e2e",
                    task_name="e2e.echo",
                    payload={"orderId": "A-1001", "qty": 3.0, "ratio": 1.0},
                    metadata={},
                    delay_s=None,
                    expires_at=None,
                )
            ),
        },
    }
    OUT_PATH.write_text(json.dumps(fixture, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {OUT_PATH} ({len(digest_vectors)} digest, {len(cursor_vectors)} cursor vectors)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
