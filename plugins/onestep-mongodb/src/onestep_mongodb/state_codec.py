from __future__ import annotations

import json
from typing import Any

from bson import json_util


def encode_state(value: Any) -> dict[str, Any]:
    return {"extended_json": json.loads(json_util.dumps(value, json_options=json_util.CANONICAL_JSON_OPTIONS))}


def decode_state(value: Any) -> Any:
    if not isinstance(value, dict) or "extended_json" not in value:
        raise ValueError("MongoDB state must contain extended_json")
    return json_util.loads(
        json.dumps(value["extended_json"]),
        json_options=json_util.JSONOptions(tz_aware=True),
    )
