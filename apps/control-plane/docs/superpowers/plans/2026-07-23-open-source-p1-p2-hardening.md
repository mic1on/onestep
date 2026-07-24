# Open-Source P1/P2 Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve the P1/P2 open-source release findings without changing the public control-plane protocol.

**Architecture:** Keep `docker-compose.yml` as a localhost-only development stack and leave the deploy Compose file as the production entry point. Add database-backed authentication throttle state and SQL-based Prometheus aggregation behind a small process-local response cache. Tighten workflow permissions, remove the unusable remote release workflow, and make modal/focus behavior explicit in the existing React hooks and components.

**Tech Stack:** FastAPI, SQLAlchemy 2, Alembic, PostgreSQL/SQLite test database, React 19, Vitest, Playwright, Docker Compose, GitHub Actions.

---

## File Structure

- `LICENSE`: repository-level MIT grant.
- `docker-compose.yml`, `.env.example`, `README.md`: local-only exposure and accurate setup guidance.
- `backend/src/onestep_control_plane_api/db/models.py`: login throttle persistence model.
- `backend/alembic/versions/202607230001_add_console_login_throttles.py`: schema migration.
- `backend/src/onestep_control_plane_api/auth/service.py`, `api/routers/auth.py`, `core/settings.py`: throttle policy, state changes and settings.
- `backend/src/onestep_control_plane_api/api/routers/prometheus.py`: SQL aggregation and TTL cache.
- `backend/tests/test_console_auth.py`, `backend/tests/test_prometheus_exporter.py`, `backend/tests/test_migrations.py`: backend regression coverage.
- `.github/workflows/ci.yml`, `.github/workflows/release-smoke.yml`: least privilege and reproducible CI only.
- `frontend/src/components/useDismissibleMenu.ts`, `MobileNavigation.tsx`, `TopologyFlow.tsx`: modal focus containment and keyboard focus styles.
- `frontend/src/components/useDismissibleMenu.test.tsx`, `TopologyFlow.test.tsx`: accessibility regression coverage.

### Task 1: Make Local Startup Explicitly Local And Add A License

**Files:**
- Create: `LICENSE`
- Modify: `docker-compose.yml`, `.env.example`, `README.md`
- Test: `docker compose --env-file .env.example -f docker-compose.yml config`

- [ ] **Step 1: Add the MIT license text**

Create `LICENSE` with the standard MIT grant and copyright holder/year matching
the project metadata. Do not rely only on `pyproject.toml` package metadata.

- [ ] **Step 2: Bind development ports to loopback**

Change the local Compose port mappings to:

```yaml
ports:
  - "127.0.0.1:${ONESTEP_CP_POSTGRES_PORT:-5432}:5432"
```

and:

```yaml
ports:
  - "127.0.0.1:${ONESTEP_CP_PORT:-4173}:8000"
```

Keep `docker-compose.deploy.yml` unchanged because it is the intended
public-facing deployment definition.

- [ ] **Step 3: Clarify local-only documentation**

Label `.env.example` as development-only and update the README's local Compose
section to state that its published ports bind only to localhost. Point users
wanting a reachable deployment to `.env.deploy.example` and
`docker-compose.deploy.yml`.

- [ ] **Step 4: Validate configuration**

Run:

```bash
docker compose --env-file .env.example -f docker-compose.yml config --quiet
docker compose --env-file .env.deploy.example -f docker-compose.deploy.yml config --quiet
git diff --check
```

Expected: all commands exit 0 and local ports render with `127.0.0.1`.

- [ ] **Step 5: Commit the deployment hardening**

```bash
git add LICENSE docker-compose.yml .env.example README.md
git commit -m "fix: restrict local control plane ports"
```

### Task 2: Add Persistent Console Login Throttling

**Files:**
- Modify: `backend/src/onestep_control_plane_api/core/settings.py`, `backend/src/onestep_control_plane_api/db/models.py`, `backend/src/onestep_control_plane_api/auth/service.py`, `backend/src/onestep_control_plane_api/api/routers/auth.py`
- Create: `backend/alembic/versions/202607230001_add_console_login_throttles.py`
- Test: `backend/tests/test_console_auth.py`, `backend/tests/test_migrations.py`

- [ ] **Step 1: Write failing login-throttle tests**

Cover these request sequences with a database-backed local user:

```python
for _ in range(settings.console_login_max_failures):
    assert client.post("/api/v1/auth/login", json=bad_credentials).status_code == 401

locked = client.post("/api/v1/auth/login", json=valid_credentials)
assert locked.status_code == 429

advance_clock_past_lockout()
assert client.post("/api/v1/auth/login", json=valid_credentials).status_code == 200
```

Also assert a successful login resets prior failure state and migration tests
include the new table and index.

- [ ] **Step 2: Run focused tests and confirm the missing behavior**

Run:

```bash
uv run pytest backend/tests/test_console_auth.py backend/tests/test_migrations.py -q
```

Expected: the new tests fail because no throttle state exists yet.

- [ ] **Step 3: Model and migrate throttle state**

Add a `ConsoleLoginThrottle` model keyed by normalized username with
`failure_count`, `window_started_at`, `locked_until`, `created_at`, and
`updated_at`. Create an Alembic migration from the current head and include an
index on `locked_until` for cleanup/query efficiency.

- [ ] **Step 4: Implement service-owned throttle transitions**

Add explicit settings:

```python
console_login_max_failures: int = Field(default=5, ge=1)
console_login_failure_window_s: int = Field(default=15 * 60, ge=60)
console_login_lockout_s: int = Field(default=15 * 60, ge=60)
```

Implement `is_console_login_locked`, `record_console_login_failure`, and
`clear_console_login_failures` in `LocalAuthService`. The router must check the
lock before password verification, record a failure after a rejected login,
clear state after success, and return `429` with a generic detail while locked.

- [ ] **Step 5: Run backend validation**

Run:

```bash
uv run ruff check
uv run pytest backend/tests/test_console_auth.py backend/tests/test_migrations.py -q
```

Expected: all focused tests pass and Ruff is clean.

- [ ] **Step 6: Commit authentication protection**

```bash
git add backend/src/onestep_control_plane_api backend/alembic/versions/202607230001_add_console_login_throttles.py backend/tests/test_console_auth.py backend/tests/test_migrations.py .env.example .env.deploy.example README.md
git commit -m "fix: throttle console login failures"
```

### Task 3: Bound Prometheus Scrape Work

**Files:**
- Modify: `backend/src/onestep_control_plane_api/api/routers/prometheus.py`, `backend/src/onestep_control_plane_api/core/settings.py`
- Test: `backend/tests/test_prometheus_exporter.py`

- [ ] **Step 1: Add failing exporter tests**

Use fixture windows from more than one service/instance and assert:

```python
first = build_prometheus_metrics(db_session)
second = build_prometheus_metrics(db_session)
assert first == second
assert "onestep_task_succeeded_total" in first
```

Instrument the session to assert the second call uses the cache, and assert the
aggregation query selects scalar columns rather than `TaskMetricWindow` or
`TaskCustomMetricWindow` ORM entities.

- [ ] **Step 2: Run the focused exporter tests**

Run:

```bash
uv run pytest backend/tests/test_prometheus_exporter.py -q
```

Expected: the new cache/ORM-materialization assertions fail before implementation.

- [ ] **Step 3: Replace Python row aggregation with SQL aggregation**

Use `select`, `func.sum`, `func.max`, `group_by`, and a latest-window subquery
to return runtime counters and gauges per exported label set. Keep
`_format_labels`, escaping, names, and deterministic ordering unchanged.

For custom metrics, group counters by service/environment/task/instance/metric
and JSON labels, then select the latest gauge value per equivalent series.

- [ ] **Step 4: Add a bounded response cache**

Add `prometheus_cache_ttl_s: float = Field(default=15.0, ge=0)` and a
module-level cache record containing body plus monotonic expiry. `0` disables
caching. Guard cache reads/writes with a lock so concurrent scrapes do not
recompute the payload simultaneously. Expose a cache-reset helper for tests
and ingestion paths.

- [ ] **Step 5: Verify behavior and formatting**

Run:

```bash
uv run ruff check
uv run pytest backend/tests/test_prometheus_exporter.py -q
```

Expected: all existing metric families and escaping tests pass, while cache-hit
tests prove no second database query.

- [ ] **Step 6: Commit exporter hardening**

```bash
git add backend/src/onestep_control_plane_api/api/routers/prometheus.py backend/src/onestep_control_plane_api/core/settings.py backend/tests/test_prometheus_exporter.py .env.example .env.deploy.example README.md
git commit -m "fix: bound prometheus scrape work"
```

### Task 4: Make CI Least-Privilege And Reproducible

**Files:**
- Modify: `.github/workflows/ci.yml`
- Delete: `.github/workflows/release-smoke.yml`

- [ ] **Step 1: Scope package write access to publishing**

Set workflow defaults to:

```yaml
permissions:
  contents: read
```

and add `packages: write` only beneath the `publish` job.

- [ ] **Step 2: Pin all Actions**

Replace each `uses: owner/action@vN` reference with its resolved immutable
40-character commit SHA and retain a trailing version comment:

```yaml
- uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4
- uses: pnpm/action-setup@f40ffcd9367d9f12939873eb1018b921a783ffaa # v4
- uses: actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020 # v4
- uses: docker/setup-qemu-action@96fe6ef7f33517b61c61be40b68a1882f3264fb8 # v4
- uses: docker/setup-buildx-action@bb05f3f5519dd87d3ba754cc423b652a5edd6d2c # v4
- uses: docker/login-action@c94ce9fb468520275223c153574b00df6fe4bcc9 # v3
- uses: docker/metadata-action@dc802804100637a589fabce1cb79ff13a1411302 # v6
- uses: docker/build-push-action@10e90e3645eae34f1e60eeb005ba3a3d33f178e8 # v6
```

- [ ] **Step 3: Remove the unusable GitHub-hosted release smoke workflow**

Delete `.github/workflows/release-smoke.yml`. Preserve the local
`scripts/release-preflight.sh` and `scripts/run-smoke.sh` paths documented for
operator-run deployment verification.

- [ ] **Step 4: Validate workflow syntax and permissions**

Run:

```bash
git diff --check
rg 'uses: .*@v[0-9]' .github/workflows && exit 1 || true
```

Expected: no whitespace errors and no mutable action version tags remain.

- [ ] **Step 5: Commit CI hardening**

```bash
git add .github/workflows/ci.yml .github/workflows/release-smoke.yml
git commit -m "ci: restrict release permissions"
```

### Task 5: Fix Keyboard Modal And Topology Focus Behavior

**Files:**
- Modify: `frontend/src/components/useDismissibleMenu.ts`, `frontend/src/components/MobileNavigation.tsx`, `frontend/src/components/TopologyFlow.tsx`
- Test: `frontend/src/components/useDismissibleMenu.test.tsx`, `frontend/src/components/TopologyFlow.test.tsx`

- [ ] **Step 1: Add failing keyboard tests**

Add a modal fixture with two buttons and verify:

```tsx
await user.tab();
expect(firstButton).toHaveFocus();
await user.tab({ shift: true });
expect(lastButton).toHaveFocus();
```

Add topology assertions that each interactive node includes a `focus-visible`
class. Keep menu/listbox behavior covered by existing tests.

- [ ] **Step 2: Run focused frontend tests**

Run:

```bash
pnpm --dir frontend exec vitest run src/components/useDismissibleMenu.test.tsx src/components/TopologyFlow.test.tsx
```

Expected: new focus-trap and focus-style assertions fail.

- [ ] **Step 3: Add opt-in focus containment**

Extend `useDismissibleMenu` with an optional `trapFocus` flag. When enabled,
collect enabled focusable descendants, wrap Tab/Shift+Tab at the endpoints, and
restore trigger focus for every close route. Pass `trapFocus: true` only from
the mobile dialog.

- [ ] **Step 4: Restore visible topology focus**

Replace `focus:outline-hidden` on source/task/sink buttons with a consistent
`focus-visible` outline or ring on the clickable button. Do not alter selected,
hover, or reduced-motion behavior.

- [ ] **Step 5: Run frontend verification**

Run:

```bash
pnpm --dir frontend run test --run
pnpm ui:build
```

Expected: all unit tests pass and TypeScript/Vite build completes.

- [ ] **Step 6: Commit accessibility fixes**

```bash
git add frontend/src/components/useDismissibleMenu.ts frontend/src/components/MobileNavigation.tsx frontend/src/components/TopologyFlow.tsx frontend/src/components/useDismissibleMenu.test.tsx frontend/src/components/TopologyFlow.test.tsx
git commit -m "fix: contain mobile dialog focus"
```

### Task 6: Run Release-Readiness Regression Checks

**Files:**
- Modify: only files made necessary by failed checks above

- [ ] **Step 1: Run full backend and frontend checks**

```bash
uv run ruff check
uv run pytest
pnpm --dir frontend run test --run
pnpm ui:build
docker compose --env-file .env.example -f docker-compose.yml config --quiet
docker compose --env-file .env.deploy.example -f docker-compose.deploy.yml config --quiet
git diff --check
```

Expected: every command exits 0.

- [ ] **Step 2: Run browser E2E when Chromium is available**

```bash
pnpm --dir frontend exec playwright install chromium
pnpm --dir frontend run e2e
```

Expected: the mobile-navigation, chart, login and task-action browser flows pass.

- [ ] **Step 3: Review staged scope before final commit**

Run:

```bash
git status --short
git diff --check
git log --oneline -6
```

Expected: only the scoped hardening changes are staged or committed; preserve
the pre-existing `frontend/src/App.tsx` icon edit and unrelated untracked files.
