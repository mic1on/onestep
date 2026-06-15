from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from cryptography.fernet import Fernet

from onestep_control_plane_api.core.settings import settings

MASKED_SECRET = "********"


class CredentialCipher:
    def __init__(self, fernet_key: str | None = None) -> None:
        key = fernet_key.encode("ascii") if fernet_key else Fernet.generate_key()
        self._fernet = Fernet(key)

    def encrypt_json(self, value: dict[str, Any]) -> str:
        payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return self._fernet.encrypt(payload).decode("ascii")

    def decrypt_json(self, value: str) -> dict[str, Any]:
        payload = self._fernet.decrypt(value.encode("ascii"))
        loaded = json.loads(payload.decode("utf-8"))
        if not isinstance(loaded, dict):
            return {}
        return loaded


def load_cipher() -> CredentialCipher:
    if settings.pipeline_credentials_fernet_key:
        return CredentialCipher(settings.pipeline_credentials_fernet_key)
    return _ephemeral_cipher()


def mask_env_vars(env_vars: dict[str, Any]) -> dict[str, str]:
    return {key: MASKED_SECRET for key in env_vars}


def merge_masked_env_vars(
    updated: dict[str, str],
    existing: dict[str, Any],
) -> dict[str, str]:
    merged: dict[str, str] = {}
    for key, value in updated.items():
        if value == MASKED_SECRET and key in existing:
            merged[key] = str(existing[key])
        else:
            merged[key] = value
    return merged


@lru_cache(maxsize=1)
def _ephemeral_cipher() -> CredentialCipher:
    return CredentialCipher()
