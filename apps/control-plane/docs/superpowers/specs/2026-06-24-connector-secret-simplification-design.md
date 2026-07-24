# Connector Secret Simplification Design

Date: 2026-06-24
Status: Ready for user review
Owner: Codex

## Summary

Keep connector secret encryption at rest, but simplify the operator-facing
configuration. Replace the current `ONESTEP_CP_CONNECTOR_SECRET_KEY` setting,
which requires a pre-generated Fernet key, with a new
`ONESTEP_CP_CONNECTOR_SECRET` setting that accepts an ordinary string.

The backend will derive the actual Fernet-compatible key internally from that
string and continue storing connector secrets as Fernet ciphertext. This keeps
the existing connector CRUD and worker compilation flow intact while removing
the need for operators to understand Fernet key formats, base64 encoding, or
key-generation commands.

This change explicitly assumes there is no deployed or local connector data
that must remain decryptable. We will not support the old variable name or
previous ciphertext produced from `ONESTEP_CP_CONNECTOR_SECRET_KEY`.

## Goals

- Preserve at-rest encryption for connector secret fields.
- Let operators configure connector encryption with a normal string value.
- Remove Fernet-specific key-generation steps from local setup and deployment.
- Keep connector database storage format and API behavior unchanged from the
  caller's perspective.
- Minimize code churn by reusing the existing `ConnectorCipher` and Fernet
  encryption flow after a single derivation step.

## Non-Goals

- No backward compatibility for existing connector ciphertext.
- No support for both old and new environment variable names at the same time.
- No redesign of connector schemas, API responses, or masking behavior.
- No introduction of an external secret manager, KMS, or file-based key store.
- No change to worker deployment behavior beyond reading the new setting.

## Current Problem

Today the control plane expects `ONESTEP_CP_CONNECTOR_SECRET_KEY` to already be
a valid Fernet key. That creates avoidable friction:

- operators must generate a specialized value before the feature works
- the required format is easy to get wrong
- the error message points at a technical requirement rather than the real user
  intent, which is just "set a secret string so connector fields are encrypted"

The complexity is in the configuration format, not in the encryption need
itself. We want to keep encryption but move the format handling into the
backend.

## Decision

Adopt a single operator-facing setting:

```text
ONESTEP_CP_CONNECTOR_SECRET
```

This setting accepts any non-empty string. The backend derives a stable
Fernet-compatible key from that string before constructing `Fernet(...)`.

We will use a deterministic key-derivation step so the same input string always
produces the same Fernet key. Because there is no existing data to preserve, we
do not need dual-read compatibility or any migration path from the prior key
format.

## Backend Design

### Settings

In `core/settings.py`:

- replace `connector_secret_key: str = ""` with `connector_secret: str = ""`
- remove the validator that requires a preformatted Fernet key
- keep only string normalization: trim whitespace and preserve an empty string
  as "not configured"

This means configuration validation becomes about presence, not format.

### Key Derivation

In `api/connector_service.py`, add a small helper that transforms the ordinary
secret string into a Fernet-compatible key.

Requirements for the helper:

- deterministic: same input string always yields the same derived key
- ASCII-safe output suitable for `Fernet(...)`
- internal-only: callers never see or provide the derived value
- compact enough to keep the current `ConnectorCipher` structure intact

Implementation shape:

- read `settings.connector_secret`
- if empty, raise `ConnectorSecretError` with a message naming
  `ONESTEP_CP_CONNECTOR_SECRET`
- derive bytes from the plain string using a stable KDF or digest step
- convert the derived bytes into the urlsafe base64 Fernet key format
- construct `Fernet(...)` from the derived key

The connector encryption and decryption APIs remain:

- `encrypt(dict) -> str`
- `decrypt(str) -> dict`

### Error Handling

The lazy-loading behavior stays the same: the backend only errors when a code
path actually needs connector encryption or decryption.

Error text changes from:

```text
ONESTEP_CP_CONNECTOR_SECRET_KEY is not configured
```

to:

```text
ONESTEP_CP_CONNECTOR_SECRET is not configured
```

If the setting is present, derivation should not fail on ordinary strings.

## Storage And Runtime Behavior

No database schema change is required.

The `connectors.secret_encrypted` column continues to store Fernet ciphertext.
The following behaviors remain unchanged:

- connector create encrypts the secret payload before persistence
- connector read decrypts and masks secret values in API responses
- connector update decrypts the current payload, merges new values, and
  re-encrypts
- worker compilation and deployment decrypt connector secrets when building the
  runtime connector payload

Because the derived Fernet key depends entirely on
`ONESTEP_CP_CONNECTOR_SECRET`, rotating that setting without re-encrypting data
will make existing connector secrets unreadable. That is the same operational
property the current implementation already has.

## Compose And Documentation

Update the operator-facing configuration surface everywhere:

- `docker-compose.yml`
- `.env.example`
- `.env.deploy.example`
- `README.md`

Documentation should say that `ONESTEP_CP_CONNECTOR_SECRET` accepts a normal
string, for example:

```bash
ONESTEP_CP_CONNECTOR_SECRET=my-dev-secret
```

README should no longer instruct users to generate a Fernet key for connectors.
If the environment-variable reference table mentions connector secret
encryption, it should describe the new setting in plain language and note that
the backend derives the encryption key internally.

## Testing

### Backend

Update existing tests to use ordinary strings instead of generated Fernet keys.

Add or adjust tests for:

- settings accept a plain connector secret string
- blank connector secret remains allowed at config-load time
- connector CRUD still encrypts at rest and masks on read
- missing connector secret raises the renamed configuration error

The existing connector API tests should continue to verify that:

- cleartext secret values are not stored in `secret_encrypted`
- decrypted values round-trip correctly
- worker runtime connector payloads still resolve expected secret fields

### Verification Commands

- `uv run pytest backend/tests/test_settings.py backend/tests/test_connectors_api.py backend/tests/test_e2e_workflow.py`

If worker deployment tests cover connector payload resolution, include the
relevant targeted backend suite in the implementation step as well.

## Rollout And Compatibility

This design intentionally takes the simplest rollout path:

- no compatibility with `ONESTEP_CP_CONNECTOR_SECRET_KEY`
- no support for decrypting old connector ciphertext
- no migration command

That is acceptable because the user confirmed the feature has not been used yet
and there is no data to preserve.

If that assumption changes before implementation lands, this design is no
longer valid and must be revised to support either dual-read compatibility or a
one-time re-encryption path.

## Risks

- Weak user-provided secrets are still possible. This is acceptable for the
  current goal because the problem being solved is operator friction, not full
  secret-management hardening.
- Secret rotation remains destructive without data re-encryption. This is
  unchanged behavior and should be documented.
- The new name is intentionally breaking. Any deployment scripts already using
  `ONESTEP_CP_CONNECTOR_SECRET_KEY` would stop working until updated.

## Success Criteria

1. A user can set `ONESTEP_CP_CONNECTOR_SECRET=my-dev-secret` and use
   connectors successfully without generating a Fernet key.
2. Connector secrets remain encrypted at rest and masked in API responses.
3. No code path still references `ONESTEP_CP_CONNECTOR_SECRET_KEY`.
4. Local and deploy examples use only the new setting name.
5. Targeted backend tests pass with the new configuration model.

## Scope Boundary

This change is limited to connector secret configuration ergonomics. It should
not alter connector field catalogs, worker compiler schemas, remote-control
protocols, or any other control-plane payload shape.
