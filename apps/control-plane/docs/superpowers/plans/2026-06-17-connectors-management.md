# Connectors Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a workspace-level Connectors feature — CRUD connector instances grouped by type, with Fernet-encrypted secrets, exposed via API and a frontend accordion page.

**Architecture:** New `connectors` DB table (cleartext config + encrypted secrets). A `connector_service.py` handles encrypt/decrypt/mask + CRUD. A `connectors.py` router exposes the REST API. The frontend has a shared connector-type catalog (`catalog.ts`) that drives both the accordion page forms and (later) the worker builder. The page is a peer of Services and Agents.

**Tech Stack:** FastAPI + SQLAlchemy + Alembic (backend); React + React Query + react-i18next (frontend); cryptography (Fernet).

**Spec:** `docs/superpowers/specs/2026-06-17-connectors-management-design.md`

---

## File Structure

### Backend — Create
- `backend/src/onestep_control_plane_api/db/models.py` — append `Connector` model (modify)
- `backend/src/onestep_control_plane_api/services/connector_service.py` — cipher + CRUD
- `backend/src/onestep_control_plane_api/api/routers/connectors.py` — REST router
- `backend/alembic/versions/202606170001_add_connectors_table.py` — migration
- `backend/tests/test_connectors_api.py` — API tests

### Backend — Modify
- `backend/src/onestep_control_plane_api/core/settings.py` — add `connector_secret_key` + validator
- `backend/src/onestep_control_plane_api/api/routers/__init__.py` — register connectors router
- `backend/src/onestep_control_plane_api/api/schemas.py` — connector request/response models
- `backend/tests/conftest.py` — set a test Fernet key

### Frontend — Create
- `frontend/src/features/connectors/catalog.ts` — connector-type field schema (shared source of truth)
- `frontend/src/features/connectors/queries.ts` — React Query hooks
- `frontend/src/pages/connectors/ConnectorsPage.tsx` — accordion page
- `frontend/src/pages/connectors/ConnectorsPage.test.tsx` — page test

### Frontend — Modify
- `frontend/src/lib/api/client.ts` — connector API functions
- `frontend/src/lib/api/types.ts` — connector types
- `frontend/src/app/router.tsx` — route
- `frontend/src/components/layout/AppShell.tsx` — nav link
- `frontend/src/lib/i18n.ts` — strings (en + zh)
- `frontend/src/components/layout/AppShell.test.tsx` — assert nav link

---

## Task 1: Backend settings — connector secret key

**Files:**
- Modify: `backend/src/onestep_control_plane_api/core/settings.py`

- [ ] **Step 1: Add the setting field + validator**

In `backend/src/onestep_control_plane_api/core/settings.py`, add the field after `retention_run_interval_s` (line 45) and before `model_config`:

```python
    retention_run_interval_s: int = Field(default=60 * 60 * 24, ge=60)
    connector_secret_key: str = ""
```

Then add a field validator after the `normalize_database_url` validator (after line 80):

```python
    @field_validator("connector_secret_key")
    @classmethod
    def validate_connector_secret_key(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            return ""
        from cryptography.fernet import Fernet

        Fernet(stripped.encode("ascii"))
        return stripped
```

- [ ] **Step 2: Verify the app still boots**

Run: `cd onestep-control-plane && uv run python -c "from onestep_control_plane_api.core.settings import settings; print(settings.connector_secret_key)"`
Expected: prints empty string (no crash).

- [ ] **Step 3: Commit**

```bash
git add backend/src/onestep_control_plane_api/core/settings.py
git commit -m "feat: add connector_secret_key setting with Fernet validation"
```

---

## Task 2: Backend model — Connector table

**Files:**
- Modify: `backend/src/onestep_control_plane_api/db/models.py`
- Test: `backend/tests/test_migrations.py` (modify assertions)

- [ ] **Step 1: Add the Connector model**

In `backend/src/onestep_control_plane_api/db/models.py`, append after the last model class (before EOF). Use the same `JSON_TYPE`, `UTCDateTime`, `utcnow`, `mapped_column` patterns as other models:

```python
class Connector(Base):
    __tablename__ = "connectors"
    __table_args__ = (
        sa.UniqueConstraint("name", name="uq_connectors_name"),
        sa.Index("ix_connectors_type", "type"),
    )

    id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    type: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    config_json: Mapped[dict[str, object]] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    secret_encrypted: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
    )
```

- [ ] **Step 2: Run existing model/migration test to confirm nothing breaks**

Run: `cd onestep-control-plane && uv run pytest backend/tests/test_migrations.py -q`
Expected: PASS (the test uses `Base.metadata.create_all`, which now includes the new table; the table-set assertions in test_migrations don't enumerate every table, just spot-check — verify no failure).

- [ ] **Step 3: Commit**

```bash
git add backend/src/onestep_control_plane_api/db/models.py
git commit -m "feat: add Connector model with encrypted secrets"
```

---

## Task 3: Backend migration — connectors table

**Files:**
- Create: `backend/alembic/versions/202606170001_add_connectors_table.py`
- Test: `backend/tests/test_migrations.py` (add assertions)

- [ ] **Step 1: Write the migration**

Create `backend/alembic/versions/202606170001_add_connectors_table.py`:

```python
"""Add connectors table.

Revision ID: 202606170001
Revises: 202606150002
Create Date: 2026-06-17 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "202606170001"
down_revision: str | None = "202606150002"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

json_type = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def _has_table(table_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    if not _has_table("connectors"):
        op.create_table(
            "connectors",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("type", sa.String(length=64), nullable=False),
            sa.Column("config_json", json_type, nullable=False),
            sa.Column("secret_encrypted", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("name", name="uq_connectors_name"),
        )
        op.create_index("ix_connectors_type", "connectors", ["type"])


def downgrade() -> None:
    if _has_table("connectors"):
        op.drop_index("ix_connectors_type", table_name="connectors")
        op.drop_table("connectors")
```

- [ ] **Step 2: Verify the migration is discovered as a valid head**

Run: `cd onestep-control-plane && uv run python -c "from onestep_control_plane_api.ops.readiness import get_expected_migration_heads; print(get_expected_migration_heads())"`
Expected: prints `['202606170001']`.

- [ ] **Step 3: Commit**

```bash
git add backend/alembic/versions/202606170001_add_connectors_table.py
git commit -m "feat: add connectors table migration"
```

---

## Task 4: Backend service — cipher + CRUD

**Files:**
- Create: `backend/src/onestep_control_plane_api/services/connector_service.py`
- Modify: `backend/tests/conftest.py` (set test Fernet key)

- [ ] **Step 1: Add test Fernet key to conftest**

In `backend/tests/conftest.py`, inside the `client` fixture (after line 84 where `settings.worker_package_storage_dir` is set), add:

```python
    original_connector_secret_key = settings.connector_secret_key
    from cryptography.fernet import Fernet

    settings.connector_secret_key = Fernet.generate_key().decode("ascii")
```

And in the teardown block (after line 113, where the other `settings` originals are restored), add:

```python
    settings.connector_secret_key = original_connector_secret_key
```

- [ ] **Step 2: Write the connector service**

Create `backend/src/onestep_control_plane_api/services/connector_service.py`:

```python
from __future__ import annotations

import json
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
    "mysql": frozenset({"dsn"}),
    "postgres": frozenset({"dsn"}),
    "redis": frozenset({"url"}),
    "rabbitmq": frozenset({"url"}),
    "sqs": frozenset(),
    "feishu_bitable": frozenset({"app_secret"}),
    "http": frozenset({"url"}),
}

VALID_CONNECTOR_TYPES = frozenset(SENSITIVE_FIELDS_BY_TYPE.keys())

MASK = "****"


class ConnectorSecretError(RuntimeError):
    """Raised when encryption/decryption is attempted without a key configured."""


class ConnectorCipher:
    """Lazily-loaded Fernet cipher bound to the configured key."""

    def __init__(self) -> None:
        self._fernet: Fernet | None = None

    @property
    def fernet(self) -> Fernet:
        if self._fernet is None:
            key = settings.connector_secret_key.strip()
            if not key:
                raise ConnectorSecretError(
                    "ONESTEP_CP_CONNECTOR_SECRET_KEY is not configured"
                )
            self._fernet = Fernet(key.encode("ascii"))
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


def mask_secret(secret: dict[str, object]) -> dict[str, object]:
    return {key: MASK for key in secret}


def build_connector_summary(
    connector: Connector,
    *,
    include_cleartext_secret: bool = False,
) -> dict[str, object]:
    cipher = get_cipher()
    secret = cipher.decrypt(connector.secret_encrypted)
    if not include_cleartext_secret:
        secret = mask_secret(secret)
    return {
        "id": str(connector.id),
        "name": connector.name,
        "type": connector.type,
        "config": dict(connector.config_json),
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
    connector = Connector(
        id=uuid4(),
        name=name,
        type=connector_type,
        config_json=config,
        secret_encrypted=cipher.encrypt({}),
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
        connector.secret_encrypted = cipher.encrypt(current)
    db.commit()
    db.refresh(connector)
    return build_connector_summary(connector)


def delete_connector(db: Session, connector_id: UUID) -> None:
    connector = get_connector_or_404(db, connector_id)
    db.delete(connector)
    db.commit()
```

- [ ] **Step 3: Verify it imports cleanly**

Run: `cd onestep-control-plane && uv run python -c "from onestep_control_plane_api.services.connector_service import create_connector, SENSITIVE_FIELDS_BY_TYPE; print(sorted(SENSITIVE_FIELDS_BY_TYPE))"`
Expected: prints the type keys, no import error.

- [ ] **Step 4: Commit**

```bash
git add backend/src/onestep_control_plane_api/services/connector_service.py backend/tests/conftest.py
git commit -m "feat: add connector service (cipher + CRUD)"
```

---

## Task 5: Backend API schemas

**Files:**
- Modify: `backend/src/onestep_control_plane_api/api/schemas.py`

- [ ] **Step 1: Add connector request/response models**

In `backend/src/onestep_control_plane_api/api/schemas.py`, append at the end of the file:

```python
class ConnectorSummary(BaseModel):
    id: str
    name: str
    type: str
    config: dict[str, object]
    secret: dict[str, object]
    created_at: str | None = None
    updated_at: str | None = None


class ConnectorListResponse(BaseModel):
    items: list[ConnectorSummary]
    total: int


class ConnectorCreateRequest(BaseModel):
    name: str
    type: str
    config: dict[str, object] = Field(default_factory=dict)
    secret: dict[str, object] = Field(default_factory=dict)


class ConnectorUpdateRequest(BaseModel):
    name: str | None = None
    config: dict[str, object] | None = None
    secret: dict[str, object] | None = None
```

- [ ] **Step 2: Verify import**

Run: `cd onestep-control-plane && uv run python -c "from onestep_control_plane_api.api.schemas import ConnectorSummary, ConnectorCreateRequest; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 3: Commit**

```bash
git add backend/src/onestep_control_plane_api/api/schemas.py
git commit -m "feat: add connector API schemas"
```

---

## Task 6: Backend router + registration

**Files:**
- Create: `backend/src/onestep_control_plane_api/api/routers/connectors.py`
- Modify: `backend/src/onestep_control_plane_api/api/routers/__init__.py`

- [ ] **Step 1: Write the router**

Create `backend/src/onestep_control_plane_api/api/routers/connectors.py`:

```python
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from onestep_control_plane_api.api.schemas import (
    ConnectorCreateRequest,
    ConnectorListResponse,
    ConnectorSummary,
    ConnectorUpdateRequest,
)
from onestep_control_plane_api.api.security import require_console_auth
from onestep_control_plane_api.db.session import get_db_session
from onestep_control_plane_api.services.connector_service import (
    create_connector,
    delete_connector,
    get_connector_or_404,
    list_connectors,
    split_payload,
    update_connector,
)

router = APIRouter(prefix="/api/v1", tags=["connectors"])


@router.get(
    "/connectors",
    response_model=ConnectorListResponse,
    dependencies=[Depends(require_console_auth)],
)
def list_connectors_endpoint(
    type: Annotated[str | None, Query()] = None,
    db: Session = Depends(get_db_session),
) -> ConnectorListResponse:
    items = list_connectors(db, type_filter=type)
    return ConnectorListResponse(items=items, total=len(items))


@router.post(
    "/connectors",
    response_model=ConnectorSummary,
    dependencies=[Depends(require_console_auth)],
)
def create_connector_endpoint(
    request: ConnectorCreateRequest,
    db: Session = Depends(get_db_session),
) -> ConnectorSummary:
    config, secret = split_payload(request.type, {**request.config, **request.secret})
    summary = create_connector(db, name=request.name, connector_type=request.type, config=config)
    # Store the secret portion (config was stored above; secret is set via update).
    if secret:
        from onestep_control_plane_api.services.connector_service import update_connector as _update

        summary = _update(db, UUID(summary["id"]), secret=secret)
    return ConnectorSummary(**summary)


@router.get(
    "/connectors/{connector_id}",
    response_model=ConnectorSummary,
    dependencies=[Depends(require_console_auth)],
)
def get_connector_endpoint(
    connector_id: UUID,
    db: Session = Depends(get_db_session),
) -> ConnectorSummary:
    from onestep_control_plane_api.services.connector_service import build_connector_summary

    connector = get_connector_or_404(db, connector_id)
    return ConnectorSummary(**build_connector_summary(connector))


@router.put(
    "/connectors/{connector_id}",
    response_model=ConnectorSummary,
    dependencies=[Depends(require_console_auth)],
)
def update_connector_endpoint(
    connector_id: UUID,
    request: ConnectorUpdateRequest,
    db: Session = Depends(get_db_session),
) -> ConnectorSummary:
    summary = update_connector(
        db,
        connector_id,
        name=request.name,
        config=request.config,
        secret=request.secret,
    )
    return ConnectorSummary(**summary)


@router.delete(
    "/connectors/{connector_id}",
    status_code=204,
    dependencies=[Depends(require_console_auth)],
)
def delete_connector_endpoint(
    connector_id: UUID,
    db: Session = Depends(get_db_session),
) -> None:
    delete_connector(db, connector_id)
```

- [ ] **Step 2: Register the router**

In `backend/src/onestep_control_plane_api/api/routers/__init__.py`, add the import (alphabetically near the other imports) and the include. Add the import line:

```python
from onestep_control_plane_api.api.routers.connectors import router as connectors_router
```

And add the include (among the other `router.include_router(...)` calls):

```python
router.include_router(connectors_router)
```

- [ ] **Step 3: Verify the router is registered**

Run: `cd onestep-control-plane && uv run python -c "from onestep_control_plane_api.api.routers import router; print([r.path for r in router.routes if 'connector' in r.path])"`
Expected: prints the 5 connector paths.

- [ ] **Step 4: Commit**

```bash
git add backend/src/onestep_control_plane_api/api/routers/connectors.py backend/src/onestep_control_plane_api/api/routers/__init__.py
git commit -m "feat: add connectors REST router"
```

---

## Task 7: Backend API tests

**Files:**
- Create: `backend/tests/test_connectors_api.py`

- [ ] **Step 1: Write the tests**

Create `backend/tests/test_connectors_api.py`:

```python
from __future__ import annotations

from uuid import uuid4

from onestep_control_plane_api.db.models import Connector
from sqlalchemy import select


def test_create_connector_encrypts_secret_and_masks_on_read(client, db_session) -> None:
    response = client.post(
        "/api/v1/connectors",
        json={
            "name": "prod-mysql",
            "type": "mysql",
            "config": {},
            "secret": {"dsn": "mysql://user:s3cret@10.0.0.1/db"},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "prod-mysql"
    assert body["type"] == "mysql"
    # Secret is masked in the response.
    assert body["secret"] == {"dsn": "****"}

    # The DB row stores an encrypted token, not cleartext.
    row = db_session.scalar(select(Connector).where(Connector.name == "prod-mysql"))
    assert row is not None
    assert "s3cret" not in row.secret_encrypted
    assert row.secret_encrypted != ""


def test_list_connectors_filtered_by_type(client) -> None:
    client.post("/api/v1/connectors", json={"name": "db1", "type": "mysql", "config": {}, "secret": {}})
    client.post("/api/v1/connectors", json={"name": "cache1", "type": "redis", "config": {}, "secret": {}})

    response = client.get("/api/v1/connectors", params={"type": "mysql"})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "db1"


def test_update_connector_keeps_secret_when_omitted(client, db_session) -> None:
    create = client.post(
        "/api/v1/connectors",
        json={"name": "mq1", "type": "rabbitmq", "config": {}, "secret": {"url": "amqp://pw@host"}},
    )
    connector_id = create.json()["id"]

    # Update only the name, send no secret — the existing secret must persist.
    response = client.put(
        f"/api/v1/connectors/{connector_id}",
        json={"name": "mq1-renamed"},
    )
    assert response.status_code == 200
    assert response.json()["name"] == "mq1-renamed"

    from uuid import UUID

    from onestep_control_plane_api.services.connector_service import build_connector_summary

    row = db_session.get(Connector, UUID(connector_id))
    summary = build_connector_summary(row, include_cleartext_secret=True)
    assert summary["secret"] == {"url": "amqp://pw@host"}


def test_update_connector_secret_overwrites_value(client) -> None:
    create = client.post(
        "/api/v1/connectors",
        json={"name": "r1", "type": "redis", "config": {}, "secret": {"url": "redis://old@host"}},
    )
    connector_id = create.json()["id"]

    response = client.put(
        f"/api/v1/connectors/{connector_id}",
        json={"secret": {"url": "redis://new@host"}},
    )
    assert response.status_code == 200
    assert response.json()["secret"] == {"url": "****"}

    # Verify the new value is stored.
    from uuid import UUID

    from onestep_control_plane_api.services.connector_service import build_connector_summary

    row = db_session.get(Connector, UUID(connector_id))
    summary = build_connector_summary(row, include_cleartext_secret=True)
    assert summary["secret"] == {"url": "redis://new@host"}


def test_delete_connector(client) -> None:
    create = client.post(
        "/api/v1/connectors",
        json={"name": "to-delete", "type": "mysql", "config": {}, "secret": {}},
    )
    connector_id = create.json()["id"]

    response = client.delete(f"/api/v1/connectors/{connector_id}")
    assert response.status_code == 204

    gone = client.get(f"/api/v1/connectors/{connector_id}")
    assert gone.status_code == 404


def test_create_rejects_duplicate_name(client) -> None:
    client.post("/api/v1/connectors", json={"name": "dup", "type": "mysql", "config": {}, "secret": {}})
    response = client.post("/api/v1/connectors", json={"name": "dup", "type": "mysql", "config": {}, "secret": {}})
    assert response.status_code == 409


def test_create_rejects_unsupported_type(client) -> None:
    response = client.post(
        "/api/v1/connectors",
        json={"name": "bad", "type": "mongodb", "config": {}, "secret": {}},
    )
    assert response.status_code == 422
```

> Note: the tests reference `db_session` directly for the encryption-at-rest assertion. The `db_session` fixture is already provided by `conftest.py`.

- [ ] **Step 2: Run the tests**

Run: `cd onestep-control-plane && uv run pytest backend/tests/test_connectors_api.py -v`
Expected: all 7 tests PASS.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_connectors_api.py
git commit -m "test: connector API CRUD, encryption-at-rest, masking"
```

---

## Task 8: Frontend types + API client

**Files:**
- Modify: `frontend/src/lib/api/types.ts`
- Modify: `frontend/src/lib/api/client.ts`

- [ ] **Step 1: Add connector types**

In `frontend/src/lib/api/types.ts`, append:

```ts
export type ConnectorType =
  | "mysql"
  | "postgres"
  | "redis"
  | "rabbitmq"
  | "sqs"
  | "feishu_bitable"
  | "http";

export interface ConnectorSummary {
  id: string;
  name: string;
  type: ConnectorType;
  config: JsonObject;
  secret: Record<string, string>;
  created_at: string | null;
  updated_at: string | null;
}

export interface ConnectorListResponse {
  items: ConnectorSummary[];
  total: number;
}

export interface ConnectorCreateRequest {
  name: string;
  type: ConnectorType;
  config: JsonObject;
  secret: Record<string, string>;
}

export interface ConnectorUpdateRequest {
  name?: string;
  config?: JsonObject;
  secret?: Record<string, string>;
}
```

- [ ] **Step 2: Add API client functions**

In `frontend/src/lib/api/client.ts`, add the type imports to the existing `types` import block (add these lines among the existing type imports):

```ts
  ConnectorCreateRequest,
  ConnectorListResponse,
  ConnectorSummary,
  ConnectorType,
  ConnectorUpdateRequest,
```

Then append the functions at the end of the file:

```ts
export function listConnectors(typeFilter?: ConnectorType) {
  return request<ConnectorListResponse>("/api/v1/connectors", {
    query: typeFilter ? { type: typeFilter } : {},
  });
}

export function createConnector(payload: ConnectorCreateRequest) {
  return request<ConnectorSummary>("/api/v1/connectors", {
    method: "POST",
    body: payload,
  });
}

export function updateConnector(connectorId: string, payload: ConnectorUpdateRequest) {
  return request<ConnectorSummary>(`/api/v1/connectors/${encodeURIComponent(connectorId)}`, {
    method: "PUT",
    body: payload,
  });
}

export function deleteConnector(connectorId: string) {
  return request<void>(`/api/v1/connectors/${encodeURIComponent(connectorId)}`, {
    method: "DELETE",
  });
}
```

- [ ] **Step 3: Verify typecheck**

Run: `cd onestep-control-plane/frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/api/types.ts frontend/src/lib/api/client.ts
git commit -m "feat: add connector API client + types"
```

---

## Task 9: Frontend connector catalog

**Files:**
- Create: `frontend/src/features/connectors/catalog.ts`

- [ ] **Step 1: Write the catalog**

Create `frontend/src/features/connectors/catalog.ts`:

```ts
import type { ConnectorType } from "../../lib/api/types";

export interface ConnectorField {
  name: string;
  label: string;
  type: "text" | "password";
  required: boolean;
  placeholder?: string;
}

export interface ConnectorTypeSchema {
  label: string;
  fields: ConnectorField[];
}

export const connectorTypeSchemas: Record<ConnectorType, ConnectorTypeSchema> = {
  mysql: {
    label: "MySQL",
    fields: [
      { name: "dsn", label: "DSN", type: "password", required: true, placeholder: "mysql://user:pass@host:3306/db" },
    ],
  },
  postgres: {
    label: "PostgreSQL",
    fields: [
      { name: "dsn", label: "DSN", type: "password", required: true, placeholder: "postgresql://user:pass@host:5432/db" },
    ],
  },
  redis: {
    label: "Redis",
    fields: [
      { name: "url", label: "URL", type: "password", required: true, placeholder: "redis://host:6379/0" },
    ],
  },
  rabbitmq: {
    label: "RabbitMQ",
    fields: [
      { name: "url", label: "URL", type: "password", required: true, placeholder: "amqp://user:pass@host:5672/" },
    ],
  },
  sqs: {
    label: "AWS SQS",
    fields: [
      { name: "region_name", label: "Region", type: "text", required: false, placeholder: "us-east-1" },
    ],
  },
  feishu_bitable: {
    label: "Feishu Bitable",
    fields: [
      { name: "app_id", label: "App ID", type: "text", required: true },
      { name: "app_secret", label: "App Secret", type: "password", required: true },
      { name: "base_url", label: "Base URL", type: "text", required: false, placeholder: "https://open.feishu.cn" },
    ],
  },
  http: {
    label: "HTTP Endpoint",
    fields: [
      { name: "url", label: "URL", type: "password", required: true, placeholder: "https://..." },
    ],
  },
};

export const connectorTypeOrder: ConnectorType[] = [
  "mysql",
  "postgres",
  "redis",
  "rabbitmq",
  "sqs",
  "feishu_bitable",
  "http",
];
```

- [ ] **Step 2: Verify typecheck**

Run: `cd onestep-control-plane/frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/features/connectors/catalog.ts
git commit -m "feat: add connector type catalog schema"
```

---

## Task 10: Frontend queries

**Files:**
- Create: `frontend/src/features/connectors/queries.ts`

- [ ] **Step 1: Write the queries**

Create `frontend/src/features/connectors/queries.ts`:

```ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createConnector,
  deleteConnector,
  listConnectors,
  updateConnector,
} from "../../lib/api/client";
import type {
  ConnectorCreateRequest,
  ConnectorType,
  ConnectorUpdateRequest,
} from "../../lib/api/types";

export const CONNECTORS_QUERY_KEY = ["connectors"];

export function useConnectorsQuery(typeFilter?: ConnectorType) {
  return useQuery({
    queryKey: typeFilter ? ["connectors", typeFilter] : CONNECTORS_QUERY_KEY,
    queryFn: () => listConnectors(typeFilter),
  });
}

export function useCreateConnectorMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: ConnectorCreateRequest) => createConnector(payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: CONNECTORS_QUERY_KEY }),
  });
}

export function useUpdateConnectorMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: ConnectorUpdateRequest }) =>
      updateConnector(id, payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: CONNECTORS_QUERY_KEY }),
  });
}

export function useDeleteConnectorMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteConnector(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: CONNECTORS_QUERY_KEY }),
  });
}
```

- [ ] **Step 2: Verify typecheck**

Run: `cd onestep-control-plane/frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/features/connectors/queries.ts
git commit -m "feat: add connector React Query hooks"
```

---

## Task 11: Frontend Connectors page

**Files:**
- Create: `frontend/src/pages/connectors/ConnectorsPage.tsx`

- [ ] **Step 1: Write the page**

Create `frontend/src/pages/connectors/ConnectorsPage.tsx`:

```tsx
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { EmptyState } from "../../components/ui/EmptyState";
import { SignalConsoleHeader } from "../../components/ui/SignalConsoleHeader";
import {
  connectorTypeOrder,
  connectorTypeSchemas,
} from "../../features/connectors/catalog";
import {
  useConnectorsQuery,
  useCreateConnectorMutation,
  useDeleteConnectorMutation,
  useUpdateConnectorMutation,
} from "../../features/connectors/queries";
import type { ConnectorSummary, ConnectorType } from "../../lib/api/types";
import { formatDateTime } from "../../lib/formatters";

type Draft = {
  name: string;
  config: Record<string, string>;
  secret: Record<string, string>;
};

function emptyDraft(): Draft {
  return { name: "", config: {}, secret: {} };
}

export function ConnectorsPage() {
  const { t } = useTranslation();
  const connectorsQuery = useConnectorsQuery();
  const createMutation = useCreateConnectorMutation();
  const updateMutation = useUpdateConnectorMutation();
  const deleteMutation = useDeleteConnectorMutation();
  const [expandedType, setExpandedType] = useState<ConnectorType | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft>(emptyDraft());

  const connectors = connectorsQuery.data?.items ?? [];
  const byType = (type: ConnectorType) => connectors.filter((c) => c.type === type);

  function startCreate(type: ConnectorType) {
    setEditingId(null);
    setDraft(emptyDraft());
    setExpandedType(type);
  }

  function startEdit(connector: ConnectorSummary) {
    setEditingId(connector.id);
    const config: Record<string, string> = {};
    const secret: Record<string, string> = {};
    const schema = connectorTypeSchemas[connector.type];
    for (const field of schema.fields) {
      const source = field.type === "password" ? connector.secret : connector.config;
      const value = source[field.name];
      if (field.type === "password") {
        secret[field.name] = value === "****" ? "" : (value ?? "");
      } else {
        config[field.name] = (value as string) ?? "";
      }
    }
    setDraft({ name: connector.name, config, secret });
    setExpandedType(connector.type);
  }

  function resetForm() {
    setEditingId(null);
    setDraft(emptyDraft());
  }

  async function handleSubmit(type: ConnectorType) {
    if (!draft.name.trim()) return;
    const config: Record<string, unknown> = { ...draft.config };
    const secret: Record<string, unknown> = { ...draft.secret };
    if (editingId) {
      await updateMutation.mutateAsync({ id: editingId, payload: { name: draft.name, config, secret } });
    } else {
      await createMutation.mutateAsync({ name: draft.name, type, config: config as never, secret: secret as never });
    }
    resetForm();
  }

  return (
    <div className="ref-console-page connectors-page">
      <SignalConsoleHeader
        kicker={t("connectors.eyebrow")}
        title={t("connectors.title")}
        description={<p className="signal-console-hero-note">{t("connectors.subtitle")}</p>}
      />

      {connectorsQuery.error ? (
        <EmptyState title={t("connectors.loadErrorTitle")} body={String(connectorsQuery.error)} />
      ) : null}
      {connectorsQuery.isPending ? (
        <div className="loading-block">{t("connectors.loading")}</div>
      ) : null}

      <section className="ref-table-card">
        <div className="ref-table-body">
          {connectorTypeOrder.map((type) => {
            const schema = connectorTypeSchemas[type];
            const items = byType(type);
            const isOpen = expandedType === type;
            return (
              <article className="ref-table-row" key={type}>
                <button
                  type="button"
                  className="ref-accordion-header"
                  onClick={() => setExpandedType(isOpen ? null : type)}
                >
                  <strong>{schema.label}</strong>
                  <span className="ref-summary-chip">{items.length}</span>
                </button>

                {isOpen ? (
                  <div className="ref-accordion-body">
                    {items.map((connector) => (
                      <div className="ref-accordion-item" key={connector.id}>
                        <div className="ref-meta-cell">
                          <strong>{connector.name}</strong>
                          <span>{formatDateTime(connector.created_at)}</span>
                        </div>
                        <div className="ref-page-actions">
                          <button type="button" onClick={() => startEdit(connector)}>
                            {t("connectors.actionEdit")}
                          </button>
                          <button
                            type="button"
                            disabled={deleteMutation.isPending}
                            onClick={() => void deleteMutation.mutateAsync(connector.id)}
                          >
                            {t("connectors.actionDelete")}
                          </button>
                        </div>
                      </div>
                    ))}

                    <div className="ref-inline-form">
                      <h4>{editingId ? t("connectors.formEdit") : t("connectors.formNew", { type: schema.label })}</h4>
                      <label className="ref-inline-control">
                        <span>{t("connectors.fieldName")}</span>
                        <input
                          type="text"
                          value={draft.name}
                          onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                        />
                      </label>
                      {schema.fields.map((field) => (
                        <label className="ref-inline-control" key={field.name}>
                          <span>{field.label}</span>
                          <input
                            type={field.type === "password" ? "password" : "text"}
                            placeholder={
                              field.type === "password" && editingId
                                ? t("connectors.leaveUnchanged")
                                : field.placeholder
                            }
                            value={
                              (field.type === "password" ? draft.secret : draft.config)[field.name] ?? ""
                            }
                            onChange={(e) => {
                              const target = field.type === "password" ? "secret" : "config";
                              setDraft({
                                ...draft,
                                [target]: { ...draft[target], [field.name]: e.target.value },
                              });
                            }}
                          />
                        </label>
                      ))}
                      <div className="ref-page-actions">
                        <button
                          type="button"
                          disabled={createMutation.isPending || updateMutation.isPending}
                          onClick={() => void handleSubmit(type)}
                        >
                          {editingId ? t("connectors.actionSave") : t("connectors.actionCreate")}
                        </button>
                        {editingId ? (
                          <button type="button" onClick={resetForm}>
                            {t("common.cancel")}
                          </button>
                        ) : (
                          <button type="button" onClick={() => startCreate(type)}>
                            {t("connectors.actionNew")}
                          </button>
                        )}
                      </div>
                    </div>
                  </div>
                ) : null}
              </article>
            );
          })}
        </div>
      </section>
    </div>
  );
}
```

- [ ] **Step 2: Verify typecheck**

Run: `cd onestep-control-plane/frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/connectors/ConnectorsPage.tsx
git commit -m "feat: add Connectors accordion page"
```

---

## Task 12: Frontend route + nav + i18n

**Files:**
- Modify: `frontend/src/app/router.tsx`
- Modify: `frontend/src/components/layout/AppShell.tsx`
- Modify: `frontend/src/lib/i18n.ts`
- Modify: `frontend/src/components/layout/AppShell.test.tsx`

- [ ] **Step 1: Add the route**

In `frontend/src/app/router.tsx`, add the import:

```ts
import { ConnectorsPage } from "../pages/connectors/ConnectorsPage";
```

Add the route inside the `AppShell` children (after the `agents/:agentId` route block, before the agents events route or wherever fits alphabetically):

```tsx
          {
            path: "connectors",
            element: <ConnectorsPage />,
          },
```

- [ ] **Step 2: Add the nav link**

In `frontend/src/components/layout/AppShell.tsx`, add a Connectors `NavLink` between the Services and Agents links:

```tsx
            <NavLink
              className={({ isActive }) =>
                isActive ? "shell-nav-link active" : "shell-nav-link"
              }
              to="/connectors"
            >
              {t("app.connectorsNav")}
            </NavLink>
```

- [ ] **Step 3: Add i18n keys (en)**

In `frontend/src/lib/i18n.ts`, in the `en` `app` block, add:

```ts
        connectorsNav: "Connectors",
```

And add a `connectors` namespace in the `en` block (near the `agentsList` namespace):

```ts
      connectors: {
        eyebrow: "Workspace",
        title: "Connectors",
        subtitle: "Shared connection configurations referenced by worker sources and sinks.",
        loading: "Loading connectors...",
        loadErrorTitle: "Unable to load connectors",
        fieldName: "Name",
        formNew: "New {{type}} connector",
        formEdit: "Edit connector",
        leaveUnchanged: "leave unchanged",
        actionNew: "New",
        actionCreate: "Create",
        actionEdit: "Edit",
        actionSave: "Save",
        actionDelete: "Delete",
      },
```

- [ ] **Step 4: Add i18n keys (zh)**

In the `zh` `app` block, add:

```ts
        connectorsNav: "连接器",
```

And add a `connectors` namespace in the `zh` block:

```ts
      connectors: {
        eyebrow: "工作区",
        title: "连接器",
        subtitle: "共享的连接配置，供 worker 的 source 和 sink 引用。",
        loading: "正在加载连接器...",
        loadErrorTitle: "无法加载连接器",
        fieldName: "名称",
        formNew: "新建 {{type}} 连接器",
        formEdit: "编辑连接器",
        leaveUnchanged: "保持不变",
        actionNew: "新建",
        actionCreate: "创建",
        actionEdit: "编辑",
        actionSave: "保存",
        actionDelete: "删除",
      },
```

- [ ] **Step 5: Update AppShell test**

In `frontend/src/components/layout/AppShell.test.tsx`, in the test that asserts nav links render, add an assertion for the Connectors link (after the existing `findByText("Services page")` block):

```tsx
    expect(screen.getByRole("link", { name: "Connectors" })).toBeInTheDocument();
```

- [ ] **Step 6: Verify typecheck + test**

Run: `cd onestep-control-plane/frontend && npx tsc --noEmit && npx vitest run src/components/layout/AppShell.test.tsx`
Expected: no type errors; AppShell test PASS.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/app/router.tsx frontend/src/components/layout/AppShell.tsx frontend/src/lib/i18n.ts frontend/src/components/layout/AppShell.test.tsx
git commit -m "feat: add Connectors route, nav link, i18n"
```

---

## Task 13: Frontend page test

**Files:**
- Create: `frontend/src/pages/connectors/ConnectorsPage.test.tsx`

- [ ] **Step 1: Write the test**

Create `frontend/src/pages/connectors/ConnectorsPage.test.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { ConnectorsPage } from "./ConnectorsPage";

const mockUseConnectorsQuery = vi.fn();
const mockCreate = vi.fn();
const mockUpdate = vi.fn();
const mockDelete = vi.fn();

vi.mock("../../features/connectors/queries", () => ({
  useConnectorsQuery: () => mockUseConnectorsQuery(),
  useCreateConnectorMutation: () => ({ isPending: false, mutateAsync: mockCreate }),
  useUpdateConnectorMutation: () => ({ isPending: false, mutateAsync: mockUpdate }),
  useDeleteConnectorMutation: () => ({ isPending: false, mutateAsync: mockDelete }),
}));

function renderPage() {
  const queryClient = new QueryClient();
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <ConnectorsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ConnectorsPage", () => {
  it("renders the header and type accordions", () => {
    mockUseConnectorsQuery.mockReturnValue({
      data: { items: [], total: 0 },
      isPending: false,
      error: null,
    });

    renderPage();

    expect(screen.getByRole("heading", { name: "Connectors" })).toBeInTheDocument();
    expect(screen.getByText("MySQL")).toBeInTheDocument();
    expect(screen.getByText("Redis")).toBeInTheDocument();
  });

  it("renders existing connectors grouped by type", () => {
    mockUseConnectorsQuery.mockReturnValue({
      data: {
        items: [
          {
            id: "11111111-1111-1111-1111-111111111111",
            name: "prod-db",
            type: "mysql",
            config: {},
            secret: { dsn: "****" },
            created_at: "2026-06-17T00:00:00Z",
            updated_at: "2026-06-17T00:00:00Z",
          },
        ],
        total: 1,
      },
      isPending: false,
      error: null,
    });

    renderPage();

    expect(screen.getByText("prod-db")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run the test**

Run: `cd onestep-control-plane/frontend && npx vitest run src/pages/connectors/ConnectorsPage.test.tsx`
Expected: 2 tests PASS.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/connectors/ConnectorsPage.test.tsx
git commit -m "test: Connectors page renders header, accordions, items"
```

---

## Task 14: Full validation

- [ ] **Step 1: Backend full test suite**

Run: `cd onestep-control-plane && uv run pytest backend/tests/ -q`
Expected: all PASS (including the 7 new connector tests).

- [ ] **Step 2: Frontend typecheck + full test suite**

Run: `cd onestep-control-plane/frontend && npx tsc --noEmit && npx vitest run`
Expected: no type errors; all tests PASS.

- [ ] **Step 3: Frontend production build**

Run: `cd onestep-control-plane/frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 4: Final commit if anything remains**

```bash
git add -A
git commit -m "chore: connectors management — full validation pass"
```
