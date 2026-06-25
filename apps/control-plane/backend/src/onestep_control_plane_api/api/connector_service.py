from __future__ import annotations

import base64
import hashlib
import json
from urllib.parse import quote, unquote, urlsplit
from uuid import UUID, uuid4

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from onestep_control_plane_api.core.settings import settings
from onestep_control_plane_api.db.models import Connector

#: Fields that must be encrypted, keyed by connector type. Both this dict and
#: the frontend catalog.ts must agree — a field listed here is stored encrypted,
#: everything else in the payload goes to config_json (cleartext).
SENSITIVE_FIELDS_BY_TYPE: dict[str, frozenset[str]] = {
    "mysql": frozenset({"dsn", "password"}),
    "postgres": frozenset({"dsn", "password"}),
    "redis": frozenset({"url", "password"}),
    "rabbitmq": frozenset({"url", "password"}),
    "sqs": frozenset(),
    "feishu_bitable": frozenset({"app_secret"}),
    "http": frozenset({"url"}),
}

VALID_CONNECTOR_TYPES = frozenset(SENSITIVE_FIELDS_BY_TYPE.keys())

MASK = "****"

RUNTIME_SECRET_FIELDS_BY_TYPE: dict[str, frozenset[str]] = {
    "mysql": frozenset({"dsn"}),
    "postgres": frozenset({"dsn"}),
    "redis": frozenset({"url"}),
    "rabbitmq": frozenset({"url"}),
    "sqs": frozenset(),
    "feishu_bitable": frozenset({"app_secret"}),
    "http": frozenset({"url"}),
}


class ConnectorSecretError(RuntimeError):
    """Raised when encryption/decryption is attempted without a key configured."""


def _derive_fernet_key(secret: str) -> bytes:
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


class ConnectorCipher:
    """Lazily-loaded Fernet cipher bound to the configured key."""

    def __init__(self) -> None:
        self._fernet: Fernet | None = None

    @property
    def fernet(self) -> Fernet:
        if self._fernet is None:
            secret = settings.connector_secret.strip()
            if not secret:
                raise ConnectorSecretError(
                    "ONESTEP_CP_CONNECTOR_SECRET is not configured"
                )
            self._fernet = Fernet(_derive_fernet_key(secret))
        return self._fernet

    def encrypt(self, data: dict[str, object]) -> str:
        return self.fernet.encrypt(json.dumps(data).encode("utf-8")).decode("ascii")

    def decrypt(self, token: str) -> dict[str, object]:
        try:
            return json.loads(self.fernet.decrypt(token.encode("ascii")))
        except InvalidToken:
            raise ConnectorSecretError("connector secret could not be decrypted") from None


_cipher: ConnectorCipher | None = None


def get_cipher() -> ConnectorCipher:
    global _cipher
    if _cipher is None:
        _cipher = ConnectorCipher()
    return _cipher


def _reset_cipher() -> None:
    """Test helper: force the cipher to re-read the key after settings change."""
    global _cipher
    _cipher = None


def split_payload(
    connector_type: str, payload: dict[str, object]
) -> tuple[dict[str, object], dict[str, object]]:
    """Split a request payload into (cleartext config, sensitive secret)."""
    sensitive = SENSITIVE_FIELDS_BY_TYPE.get(connector_type, frozenset())
    config: dict[str, object] = {}
    secret: dict[str, object] = {}
    for key, value in payload.items():
        if key in sensitive:
            secret[key] = value
        else:
            config[key] = value
    return config, secret


def _str_value(mapping: dict[str, object], key: str) -> str:
    value = mapping.get(key)
    if value is None:
        return ""
    return str(value).strip()


def _drop_empty_values(mapping: dict[str, object]) -> dict[str, object]:
    return {
        key: value
        for key, value in mapping.items()
        if value is not None and value != ""
    }


def _quote_url_part(value: str) -> str:
    return quote(value, safe="")


def _netloc(config: dict[str, object], secret: dict[str, object]) -> str:
    host = _str_value(config, "host")
    port = _str_value(config, "port")
    username = _str_value(config, "username")
    password = _str_value(secret, "password")
    auth = ""
    if username or password:
        auth = _quote_url_part(username)
        if password:
            auth = f"{auth}:{_quote_url_part(password)}"
        auth = f"{auth}@"
    return f"{auth}{host}{f':{port}' if port else ''}"


def _build_database_dsn(
    connector_type: str, config: dict[str, object], secret: dict[str, object]
) -> str | None:
    host = _str_value(config, "host")
    database = _str_value(config, "database")
    if not host or not database:
        return None
    scheme = "postgresql" if connector_type == "postgres" else "mysql"
    return f"{scheme}://{_netloc(config, secret)}/{_quote_url_part(database)}"


def _build_redis_url(config: dict[str, object], secret: dict[str, object]) -> str | None:
    host = _str_value(config, "host")
    if not host:
        return None
    database = _str_value(config, "database")
    return f"redis://{_netloc(config, secret)}{f'/{_quote_url_part(database)}' if database else ''}"


def _build_rabbitmq_url(config: dict[str, object], secret: dict[str, object]) -> str | None:
    host = _str_value(config, "host")
    if not host:
        return None
    vhost = _str_value(config, "vhost")
    return f"amqp://{_netloc(config, secret)}{f'/{_quote_url_part(vhost)}' if vhost else ''}"


def _display_parts_from_url(
    value: object,
    *,
    database_key: str = "database",
) -> dict[str, object]:
    if not isinstance(value, str) or not value:
        return {}
    parsed = urlsplit(value)
    parts: dict[str, object] = {}
    if parsed.hostname:
        parts["host"] = parsed.hostname
    if parsed.port is not None:
        parts["port"] = str(parsed.port)
    if parsed.username:
        parts["username"] = unquote(parsed.username)
    path_value = parsed.path.lstrip("/")
    if path_value:
        parts[database_key] = unquote(path_value)
    return parts


def _secret_parts_from_url(value: object) -> dict[str, object]:
    if not isinstance(value, str) or not value:
        return {}
    parsed = urlsplit(value)
    if parsed.password is None:
        return {}
    return {"password": unquote(parsed.password)}


def normalize_connector_storage(
    connector_type: str,
    config: dict[str, object],
    secret: dict[str, object],
    *,
    prefer_runtime_url: bool = False,
) -> tuple[dict[str, object], dict[str, object]]:
    """Normalize display fields and compiled runtime credentials for storage."""
    config = _drop_empty_values(dict(config))
    secret = _drop_empty_values(dict(secret))

    if connector_type in {"mysql", "postgres"}:
        if "dsn" in secret and (prefer_runtime_url or "host" not in config):
            parts = _display_parts_from_url(secret["dsn"])
            config = {**config, **parts} if prefer_runtime_url else {**parts, **config}
        if "dsn" in secret and (prefer_runtime_url or "password" not in secret):
            parts = _secret_parts_from_url(secret["dsn"])
            secret = {**secret, **parts} if prefer_runtime_url else {**parts, **secret}
        dsn = _build_database_dsn(connector_type, config, secret)
        if dsn:
            secret["dsn"] = dsn
    elif connector_type == "redis":
        if "url" in secret and (prefer_runtime_url or "host" not in config):
            parts = _display_parts_from_url(secret["url"])
            config = {**config, **parts} if prefer_runtime_url else {**parts, **config}
        if "url" in secret and (prefer_runtime_url or "password" not in secret):
            parts = _secret_parts_from_url(secret["url"])
            secret = {**secret, **parts} if prefer_runtime_url else {**parts, **secret}
        url = _build_redis_url(config, secret)
        if url:
            secret["url"] = url
    elif connector_type == "rabbitmq":
        if "url" in secret and (prefer_runtime_url or "host" not in config):
            parts = _display_parts_from_url(secret["url"], database_key="vhost")
            config = {**config, **parts} if prefer_runtime_url else {**parts, **config}
        if "url" in secret and (prefer_runtime_url or "password" not in secret):
            parts = _secret_parts_from_url(secret["url"])
            secret = {**secret, **parts} if prefer_runtime_url else {**parts, **secret}
        url = _build_rabbitmq_url(config, secret)
        if url:
            secret["url"] = url

    return config, secret


def _display_config(
    connector_type: str,
    config: dict[str, object],
    secret: dict[str, object],
) -> dict[str, object]:
    if "host" in config:
        return config
    if connector_type in {"mysql", "postgres"} and "dsn" in secret:
        return {**_display_parts_from_url(secret["dsn"]), **config}
    if connector_type == "redis" and "url" in secret:
        return {**_display_parts_from_url(secret["url"]), **config}
    if connector_type == "rabbitmq" and "url" in secret:
        return {
            **_display_parts_from_url(secret["url"], database_key="vhost"),
            **config,
        }
    return config


def mask_secret(secret: dict[str, object]) -> dict[str, object]:
    return {key: MASK for key in secret}


def build_runtime_connector_payload(connector: Connector) -> dict[str, object]:
    """Return only the fields expected by generated worker.yaml resources."""
    cipher = get_cipher()
    secret = cipher.decrypt(connector.secret_encrypted)
    runtime_secret_fields = RUNTIME_SECRET_FIELDS_BY_TYPE.get(connector.type, frozenset())
    runtime_secret = {
        key: value for key, value in secret.items() if key in runtime_secret_fields
    }
    runtime_config = dict(connector.config_json)
    if runtime_secret_fields:
        runtime_config = {}
    return {
        "type": connector.type,
        "config": runtime_config,
        "secret": runtime_secret,
    }


def build_connector_summary(
    connector: Connector,
    *,
    include_cleartext_secret: bool = False,
) -> dict[str, object]:
    cipher = get_cipher()
    secret = cipher.decrypt(connector.secret_encrypted)
    config = _display_config(connector.type, dict(connector.config_json), secret)
    if not include_cleartext_secret:
        secret = mask_secret(secret)
    return {
        "id": str(connector.id),
        "name": connector.name,
        "type": connector.type,
        "config": config,
        "secret": secret,
        "created_at": connector.created_at.isoformat() if connector.created_at else None,
        "updated_at": connector.updated_at.isoformat() if connector.updated_at else None,
    }


def list_connectors(
    db: Session, *, type_filter: str | None = None
) -> list[dict[str, object]]:
    stmt = select(Connector).order_by(Connector.type, Connector.name)
    if type_filter:
        stmt = stmt.where(Connector.type == type_filter)
    rows = db.execute(stmt).scalars().all()
    return [build_connector_summary(row) for row in rows]


def get_connector_or_404(db: Session, connector_id: UUID) -> Connector:
    connector = db.get(Connector, connector_id)
    if connector is None:
        raise HTTPException(status_code=404, detail="connector not found")
    return connector


def create_connector(
    db: Session,
    *,
    name: str,
    connector_type: str,
    config: dict[str, object],
    secret: dict[str, object] | None = None,
) -> dict[str, object]:
    if connector_type not in VALID_CONNECTOR_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"unsupported connector type: {connector_type}",
        )
    existing = db.scalar(select(Connector).where(Connector.name == name))
    if existing is not None:
        raise HTTPException(status_code=409, detail="connector name already exists")
    cipher = get_cipher()
    config, secret = normalize_connector_storage(connector_type, config, secret or {})
    connector = Connector(
        id=uuid4(),
        name=name,
        type=connector_type,
        config_json=config,
        secret_encrypted=cipher.encrypt(secret),
    )
    db.add(connector)
    db.commit()
    db.refresh(connector)
    return build_connector_summary(connector)


def update_connector(
    db: Session,
    connector_id: UUID,
    *,
    name: str | None = None,
    config: dict[str, object] | None = None,
    secret: dict[str, object] | None = None,
) -> dict[str, object]:
    connector = get_connector_or_404(db, connector_id)
    incoming_secret_keys = set(secret or {})
    if name is not None:
        existing = db.scalar(
            select(Connector).where(Connector.name == name, Connector.id != connector_id)
        )
        if existing is not None:
            raise HTTPException(status_code=409, detail="connector name already exists")
        connector.name = name
    if config is not None:
        connector.config_json = config
    if secret is not None:
        cipher = get_cipher()
        current = cipher.decrypt(connector.secret_encrypted)
        # An empty/None value in the secret payload means "leave unchanged".
        for key, value in secret.items():
            if value in (None, ""):
                continue
            current[key] = value
        secret = current
    else:
        cipher = get_cipher()
        secret = cipher.decrypt(connector.secret_encrypted)
    runtime_secret_fields = RUNTIME_SECRET_FIELDS_BY_TYPE.get(connector.type, frozenset())
    connector.config_json, normalized_secret = normalize_connector_storage(
        connector.type,
        dict(connector.config_json),
        dict(secret),
        prefer_runtime_url=config is None and bool(incoming_secret_keys & runtime_secret_fields),
    )
    connector.secret_encrypted = cipher.encrypt(normalized_secret)
    db.commit()
    db.refresh(connector)
    return build_connector_summary(connector)


def delete_connector(db: Session, connector_id: UUID) -> None:
    connector = get_connector_or_404(db, connector_id)
    db.delete(connector)
    db.commit()
