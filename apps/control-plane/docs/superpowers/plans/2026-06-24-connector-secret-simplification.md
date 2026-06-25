# Connector Secret Simplification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the connector Fernet-key configuration with a normal operator-facing secret string while keeping connector secrets encrypted at rest.

**Architecture:** Rename the setting from `ONESTEP_CP_CONNECTOR_SECRET_KEY` to `ONESTEP_CP_CONNECTOR_SECRET`, derive the Fernet-compatible key inside `connector_service`, and leave connector CRUD storage behavior unchanged. Update targeted backend tests, compose examples, and README so operators only need to provide a plain string.

**Tech Stack:** FastAPI, Pydantic Settings, cryptography/Fernet, pytest, Docker Compose.

---

## What Already Exists

- `backend/src/onestep_control_plane_api/core/settings.py` defines
  `connector_secret_key` and validates it by constructing `Fernet(...)`.
- `backend/src/onestep_control_plane_api/api/connector_service.py` lazily
  constructs a `ConnectorCipher` from `settings.connector_secret_key`.
- `backend/tests/conftest.py` seeds tests with a generated Fernet key.
- `backend/tests/test_connectors_api.py` and
  `backend/tests/test_e2e_workflow.py` already verify connector encryption,
  masking, and runtime payload resolution.
- `docker-compose.yml`, `.env.example`, and the repo root `.env` reference
  `ONESTEP_CP_CONNECTOR_SECRET_KEY`.
- `README.md` does not yet document the connector secret variable in the env
  table, but local examples and Compose wiring still use the old name.

## NOT In Scope

- Do not keep compatibility with `ONESTEP_CP_CONNECTOR_SECRET_KEY`.
- Do not add migrations or re-encryption helpers for old connector ciphertext.
- Do not change connector response masking, schemas, or DB columns.
- Do not add external secret-manager integrations or file-backed key storage.

## File Structure

- Modify `backend/src/onestep_control_plane_api/core/settings.py`: rename the
  settings field and simplify validation to plain-string normalization.
- Modify `backend/src/onestep_control_plane_api/api/connector_service.py`: add
  deterministic key derivation from the plain connector secret and rename the
  configuration error message.
- Modify `backend/tests/conftest.py`: seed test settings with a plain string
  instead of `Fernet.generate_key()`.
- Modify `backend/tests/test_settings.py`: add a test that a plain connector
  secret string is accepted and normalized.
- Modify `backend/tests/test_connectors_api.py`: add a focused test for the new
  missing-setting error message.
- Modify `docker-compose.yml`: inject `ONESTEP_CP_CONNECTOR_SECRET` into the
  `plane` service.
- Modify `.env.example` and `.env.deploy.example`: replace the old variable
  name with the new one.
- Modify `README.md`: add the new env var to the env table and show a normal
  string example.

---

### Task 1: Lock In The New Settings Contract With Failing Tests

**Files:**
- Modify: `backend/tests/test_settings.py`
- Modify: `backend/tests/test_connectors_api.py`
- Test: `backend/tests/test_settings.py`
- Test: `backend/tests/test_connectors_api.py`

- [ ] **Step 1: Add a plain-string settings test**

Append this test to `backend/tests/test_settings.py`:

```python
def test_settings_accept_plain_connector_secret(monkeypatch) -> None:
    monkeypatch.setenv("ONESTEP_CP_CONNECTOR_SECRET", "  my-dev-secret  ")
    monkeypatch.delenv("ONESTEP_CP_CONNECTOR_SECRET_KEY", raising=False)

    settings = Settings(_env_file=None)

    assert settings.connector_secret == "my-dev-secret"
```

- [ ] **Step 2: Add a missing-secret error test**

Add this import and test to `backend/tests/test_connectors_api.py`:

```python
from onestep_control_plane_api.api.connector_service import (
    ConnectorSecretError,
    _reset_cipher,
    build_connector_summary,
    build_runtime_connector_payload,
)
from onestep_control_plane_api.core.settings import settings
```

```python
def test_build_connector_summary_requires_connector_secret(client, db_session) -> None:
    create = client.post(
        "/api/v1/connectors",
        json={"name": "needs-secret", "type": "mysql", "config": {}, "secret": {}},
    )
    assert create.status_code == 200

    row = db_session.scalar(select(Connector).where(Connector.name == "needs-secret"))
    assert row is not None

    original_secret = settings.connector_secret
    settings.connector_secret = ""
    _reset_cipher()
    try:
        try:
            build_connector_summary(row)
        except ConnectorSecretError as exc:
            assert str(exc) == "ONESTEP_CP_CONNECTOR_SECRET is not configured"
        else:
            raise AssertionError("expected ConnectorSecretError")
    finally:
        settings.connector_secret = original_secret
        _reset_cipher()
```

- [ ] **Step 3: Run the focused tests and confirm they fail**

Run:

```bash
uv run pytest backend/tests/test_settings.py backend/tests/test_connectors_api.py -q
```

Expected: FAIL because `Settings` does not yet define `connector_secret`, and
the connector service still reports the old variable name.

- [ ] **Step 4: Commit the failing tests**

```bash
git add backend/tests/test_settings.py backend/tests/test_connectors_api.py
git commit -m "test: cover plain connector secret configuration"
```

---

### Task 2: Replace Fernet-Key Input With Plain-String Derivation

**Files:**
- Modify: `backend/src/onestep_control_plane_api/core/settings.py`
- Modify: `backend/src/onestep_control_plane_api/api/connector_service.py`
- Modify: `backend/tests/conftest.py`
- Test: `backend/tests/test_settings.py`
- Test: `backend/tests/test_connectors_api.py`

- [ ] **Step 1: Simplify the settings field**

In `backend/src/onestep_control_plane_api/core/settings.py`, replace the old
field and validator with:

```python
    connector_secret: str = ""
```

```python
    @field_validator("connector_secret", mode="before")
    @classmethod
    def normalize_connector_secret(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value
```

Remove the `validate_connector_secret_key` validator entirely.

- [ ] **Step 2: Add deterministic key derivation**

At the top of `backend/src/onestep_control_plane_api/api/connector_service.py`,
add the imports:

```python
import base64
import hashlib
```

Add this helper above `ConnectorCipher`:

```python
def _derive_fernet_key(secret: str) -> bytes:
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)
```

Update `ConnectorCipher.fernet` to read and derive from the new field:

```python
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
```

- [ ] **Step 3: Update the shared test fixture**

In `backend/tests/conftest.py`, replace the connector-secret setup block with:

```python
    original_connector_secret = settings.connector_secret
    settings.connector_secret = "test-connector-secret"
```

And on teardown restore:

```python
    settings.connector_secret = original_connector_secret
```

Remove the `Fernet.generate_key()` dependency from this fixture.

- [ ] **Step 4: Run the focused tests and confirm they pass**

Run:

```bash
uv run pytest backend/tests/test_settings.py backend/tests/test_connectors_api.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit the implementation**

```bash
git add backend/src/onestep_control_plane_api/core/settings.py \
  backend/src/onestep_control_plane_api/api/connector_service.py \
  backend/tests/conftest.py \
  backend/tests/test_settings.py \
  backend/tests/test_connectors_api.py
git commit -m "feat: simplify connector secret configuration"
```

---

### Task 3: Verify End-To-End Connector Flows Still Work

**Files:**
- Modify: none
- Test: `backend/tests/test_e2e_workflow.py`

- [ ] **Step 1: Run the connector end-to-end workflow**

Run:

```bash
uv run pytest backend/tests/test_e2e_workflow.py -q
```

Expected: PASS, proving connector creation, readback, worker compilation, and
runtime payload resolution still work with the derived Fernet key.

- [ ] **Step 2: Run the combined targeted backend suite**

Run:

```bash
uv run pytest \
  backend/tests/test_settings.py \
  backend/tests/test_connectors_api.py \
  backend/tests/test_e2e_workflow.py -q
```

Expected: PASS.

- [ ] **Step 3: Commit the verification checkpoint**

```bash
git add -A
git commit -m "test: verify connector flows with derived secret key"
```

If there are no file changes after verification, skip this commit.

---

### Task 4: Rename Operator-Facing Configuration Everywhere

**Files:**
- Modify: `docker-compose.yml`
- Modify: `.env.example`
- Modify: `.env.deploy.example`
- Modify: `README.md`

- [ ] **Step 1: Update Compose wiring**

In `docker-compose.yml`, replace:

```yaml
      ONESTEP_CP_CONNECTOR_SECRET_KEY: ${ONESTEP_CP_CONNECTOR_SECRET_KEY:-}
```

with:

```yaml
      ONESTEP_CP_CONNECTOR_SECRET: ${ONESTEP_CP_CONNECTOR_SECRET:-}
```

- [ ] **Step 2: Update example env files**

In `.env.example`, replace:

```dotenv
ONESTEP_CP_CONNECTOR_SECRET_KEY=
```

with:

```dotenv
ONESTEP_CP_CONNECTOR_SECRET=
```

In `.env.deploy.example`, add this line near the other control-plane
application settings:

```dotenv
ONESTEP_CP_CONNECTOR_SECRET=
```

- [ ] **Step 3: Update README**

In `README.md`, add the new env var to the environment-variable table and use a
plain-string explanation similar to:

```markdown
| `ONESTEP_CP_CONNECTOR_SECRET` | Backend connectors | empty | Plain string used to derive the encryption key for connector secrets at rest. Required when using the Connectors feature. Example: `my-dev-secret`. |
```

If any connector setup text still refers to `ONESTEP_CP_CONNECTOR_SECRET_KEY`
or Fernet key generation for connectors, replace it with the new plain-string
guidance.

- [ ] **Step 4: Search for stale references**

Run:

```bash
rg -n "ONESTEP_CP_CONNECTOR_SECRET_KEY|connector_secret_key" .
```

Expected: no matches in active code or docs. If the spec/plan docs intentionally
mention the old name, restrict the search to runtime files:

```bash
rg -n "ONESTEP_CP_CONNECTOR_SECRET_KEY|connector_secret_key" \
  backend docker-compose.yml .env.example .env.deploy.example README.md
```

- [ ] **Step 5: Commit the config and docs rename**

```bash
git add docker-compose.yml .env.example .env.deploy.example README.md
git commit -m "docs: rename connector secret setting"
```

---

### Task 5: Final Verification

**Files:**
- Modify: none

- [ ] **Step 1: Run the full targeted verification set**

Run:

```bash
uv run pytest \
  backend/tests/test_settings.py \
  backend/tests/test_connectors_api.py \
  backend/tests/test_e2e_workflow.py -q
```

Expected: PASS.

- [ ] **Step 2: Inspect the final diff**

Run:

```bash
git diff -- backend/src/onestep_control_plane_api/core/settings.py \
  backend/src/onestep_control_plane_api/api/connector_service.py \
  backend/tests/conftest.py \
  backend/tests/test_settings.py \
  backend/tests/test_connectors_api.py \
  docker-compose.yml \
  .env.example \
  .env.deploy.example \
  README.md
```

Expected: only the connector-secret configuration, derivation, tests, and docs
changes appear.

- [ ] **Step 3: Commit any final cleanup**

```bash
git add -A
git commit -m "chore: finalize connector secret simplification"
```

If there are no remaining file changes, skip this commit.
