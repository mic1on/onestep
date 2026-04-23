from __future__ import annotations

from typing import Any


def normalize_user(payload: dict[str, Any], *, region: str, phone_country_code: str) -> dict[str, Any]:
    phone = str(payload.get("phone") or "").strip().replace(" ", "")
    if phone and not phone.startswith("+"):
        phone = f"{phone_country_code}{phone.lstrip('0')}"

    return {
        "id": payload["id"],
        "name": str(payload["name"]).strip(),
        "email": str(payload["email"]).strip().lower(),
        "phone": phone or None,
        "region": region,
    }
