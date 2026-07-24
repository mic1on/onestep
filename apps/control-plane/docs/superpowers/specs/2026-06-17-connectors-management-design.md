# Connectors Management Design

## Summary

Add a workspace-level **Connectors** feature to the control plane: a standalone
entry where operators register and manage connection configurations to external
systems (MySQL, Redis, RabbitMQ, etc.). Each connector is a reusable entity
holding the non-sensitive config in cleartext and the sensitive fields
encrypted with Fernet. This is the first of three phases toward a Lambda-style
worker design platform — connectors are the shared dependency that the later
worker builder's source/sink dropdowns will reference.

The pipeline builder (removed in a prior change) shipped a similar concept
(`pipeline_credentials` + `CredentialCipher`) bundled inside the visual editor.
This phase restores the connection-management capability as a first-class,
shared resource — decoupled from any editor — with secrets encrypted at rest.

## Goals

- Operators can CRUD connector instances, grouped by type, from a dedicated
  page that is a peer of Services and Agents.
- Sensitive fields (DSN passwords, tokens, secrets) are encrypted at rest;
  the API never returns them in cleartext (masked in read responses).
- The connector type catalog and its per-type field schema live in one shared
  frontend definition, so the later worker builder (phase 2) and this page
  render forms from the same source of truth.
- No connection testing in this phase (explicitly deferred).

## Non-Goals

- No source/sink configuration UI (phase 2: worker builder).
- No worker.yaml synthesis / compiler (phase 2).
- No deployment integration (phase 3).
- No live connection testing — "test" buttons and pass/fail status badges are
  deferred. Format validation (required fields present) is the only check.
- No connector usage tracking / "referenced by N workers" counters.
- No connector import/export or bulk operations.

## Context

onestep's real source/sink model requires a separately-declared **connector
resource** (a connection pool) before a plugin source or sink can reference it
via `connector: <name>`. For example a `mysql_incremental` source needs a
`mysql` connector holding the DSN. Rather than re-enter credentials per worker,
connectors are workspace-level shared entities: create once, reference from
many workers.

The connector types and their config fields map directly to the real onestep
plugin connector classes discovered in the codebase:

| type | onestep class | required cleartext fields | sensitive fields |
|---|---|---|---|
| `mysql` | `MySQLConnector` | — | `dsn` |
| `postgres` | `PostgresConnector` | — | `dsn` |
| `redis` | `RedisConnector` | — | `url` |
| `rabbitmq` | `RabbitMQConnector` | — | `url` |
| `sqs` | `SQSConnector` | `region_name` (optional) | — (uses IAM) |
| `feishu_bitable` | `FeishuBitableConnector` | `app_id`, `base_url` | `app_secret` |
| `http` | (for `http_sink`) | — | `url` |

This catalog is exhaustive for the plugins currently in the onestep repo and is
encoded as a static frontend schema (not fetched from the backend), so the form
renderer can build type-specific forms without a metadata endpoint.

## Data Model

### `connectors` table

```
connectors
  id              UUID PK
  name            VARCHAR(255), NOT NULL  (unique within workspace)
  type            VARCHAR(64),  NOT NULL  (one of the catalog types)
  config_json     JSON,         NOT NULL  (cleartext non-sensitive fields)
  secret_encrypted TEXT,        NOT NULL  (Fernet-encrypted JSON of sensitive fields)
  created_at      TIMESTAMPTZ
  updated_at      TIMESTAMPTZ
```

- `config_json` holds the non-sensitive fields (e.g. `{"region_name":
  "us-east-1"}` for SQS, `{"app_id": "...", "base_url": "..."}` for feishu).
  Empty dict when a type has no cleartext fields.
- `secret_encrypted` holds the Fernet-encrypted JSON of sensitive fields (e.g.
  `{"dsn": "mysql://user:pass@host/db"}`). The row always has a value (empty
  `{}` encrypted) even for types with no sensitive fields, so the column stays
  NOT NULL and the encrypt/decrypt path is uniform.

No separate `connector_types` table — the type catalog is a frontend constant.

### Settings

A new setting provides the Fernet key:

```
ONESTEP_CP_CONNECTOR_SECRET_KEY: str = ""
```

Validated at startup (same pattern as the removed
`pipeline_credentials_fernet_key`): if non-empty it must be a valid Fernet
token; if empty, connectors with sensitive fields cannot be created and the API
returns a clear error. This keeps dev (no secrets) frictionless while requiring
a key in any environment that stores real credentials.

## Backend

### `core/settings.py`

Add `connector_secret_key: str = ""` with a `field_validator` that confirms it
is a valid Fernet token when non-empty (importing `cryptography.fernet.Fernet`
lazily inside the validator, as the prior implementation did).

### `services/connector_service.py` (new)

- `ConnectorCipher` — loads the Fernet key from settings once; `encrypt(dict) ->
  str` and `decrypt(str) -> dict`. Raises a clear `ConnectorSecretError` when
  the key is unset and encryption is attempted.
- `mask_secrets(secret_dict) -> dict` — replaces every value with `"****"` for
  API responses.
- CRUD helpers operating on the `connectors` table:
  - `list_connectors(type_filter)` — returns rows with secrets masked.
  - `get_connector(id)` — same masking.
  - `create_connector(payload)` — splits payload into config/secret by the
    field schema, encrypts secret, persists.
  - `update_connector(id, payload)` — re-encrypts; a sensitive field present as
    `None`/empty in the payload means "leave unchanged" (so the masked value
    round-trips without forcing re-entry).
  - `delete_connector(id)`.

The split of a payload into cleartext-config vs encrypted-secret is driven by
the **same** per-type field schema the frontend uses, mirrored in the backend as
a small dict (`SENSITIVE_FIELDS_BY_TYPE`). Both sides must agree on which fields
are sensitive; this is the single point to update when adding a type.

### `api/routers/connectors.py` (new)

```
GET    /api/v1/connectors?type=        list (optional type filter)
POST   /api/v1/connectors              create
GET    /api/v1/connectors/:id          detail (masked)
PUT    /api/v1/connectors/:id          update
DELETE /api/v1/connectors/:id          delete
```

Read responses never include cleartext secrets. The create/update request bodies
carry the cleartext sensitive values (over TLS); they are encrypted before
persist and never echoed back. Registered in `api/routers/__init__.py`.

### Migration

New migration `add_connectors_table`, chained off the current head
(`202606150002`). Creates the `connectors` table with a unique index on `name`
and an index on `type` (for the grouped list query).

## Frontend

### Connector type catalog (shared constant)

`features/connectors/catalog.ts` — a `Record<ConnectorType, ConnectorTypeSchema>`
where each entry has `label` and `fields: ConnectorField[]`. A `ConnectorField`
has `name`, `label`, `type: "text" | "password"`, `required`, optional
`placeholder`. This file is the single source of truth for both the connectors
page (phase 1) and the worker builder source/sink dropdowns (phase 2).

### Connectors page (`pages/connectors/ConnectorsPage.tsx`)

Accordion layout grouped by connector type, mirroring the Services list's card
styling:

- The page header uses `SignalConsoleHeader` (kicker "Workspace", title
  "Connectors").
- A section per catalog type, rendered as an accordion row: collapsed shows the
  type label + a count badge. Expanded shows the list of connector instances of
  that type (name, masked sensitive fields, created_at, edit/delete actions)
  and a "New" button that reveals an inline form.
- The inline form renders fields from `catalog.ts` for the selected type. Text
  fields show their value; password fields show `****` when editing and are
  blank (placeholder "leave unchanged") — an empty password submit means
  "keep existing".
- Empty/error/loading states use the existing `EmptyState` / `loading-block`.
- Format validation: required fields must be non-empty before submit (client
  side); the backend re-validates.

### API client + queries

`lib/api/client.ts`: `listConnectors`, `getConnector`, `createConnector`,
`updateConnector`, `deleteConnector`. `features/connectors/queries.ts`:
`useConnectorsQuery`, `useCreateConnectorMutation`,
`useUpdateConnectorMutation`, `useDeleteConnectorMutation` with cache
invalidation, following the worker-agents query pattern.

### Navigation + routes + i18n

- `app/router.tsx`: `connectors` → `ConnectorsPage`.
- `components/layout/AppShell.tsx`: a `Connectors` `NavLink` between Services and
  Agents, always visible (no role gating).
- `lib/i18n.ts`: `app.connectorsNav` ("Connectors" / "连接器") plus a
  `connectors.*` namespace for page strings, in both `en` and `zh`.

## Testing

- **Backend**: `tests/test_connectors_api.py` — CRUD happy path, masking on
  read, encryption-at-rest assertion (secret column != cleartext), update with
  omitted sensitive field keeps the old value, delete. A test that creating a
  sensitive connector without the Fernet key configured returns a clear error.
- **Frontend**: `ConnectorsPage.test.tsx` — renders grouped accordion, the
  type-specific form appears on expand, create calls the mutation; mirrors the
  existing list-page test style. Extend `AppShell.test.tsx` to assert the
  Connectors nav link.
- Migration test: extend `tests/test_migrations.py` with the `connectors` table
  presence + the unique-name index.

## Risks

- **Key management**: if `ONESTEP_CP_CONNECTOR_SECRET_KEY` is lost, all stored
  secrets become undecryptable. Document that it must be backed up; the setting
  validator fails fast at startup if the key is malformed (not silently empty in
  prod). Mitigated, not eliminated — this is inherent to at-rest encryption.
- **Catalog drift**: the frontend catalog and backend `SENSITIVE_FIELDS_BY_TYPE`
  must agree. Adding a connector type requires updating both; a missed update
  means a field is stored in cleartext when it should be encrypted (or vice
  versa). Kept to a single dict on each side to minimize drift surface.
- **No usage protection**: deleting a connector referenced by a future worker
  does not block (workers don't exist yet in phase 1). Phase 2 should add a
  reference check before allowing delete; noted here so it is not forgotten.
