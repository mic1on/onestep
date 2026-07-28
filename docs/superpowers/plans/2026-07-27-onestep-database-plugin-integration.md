# Database Plugin Shared Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate the stable Elasticsearch/OpenSearch, ClickHouse, and MongoDB plugin packages into the onestep workspace, reliability gates, live service matrices, documentation, bundled worker, and ordered `0.1.0` release path.

**Architecture:** Begin only after all three plugin-local plans produce passing independent packages with frozen `0.1.0` metadata. One integration owner makes every shared edit, regenerates the lock once, runs plugin suites in isolated processes, adds default ClickHouse/MongoDB services plus separate Elasticsearch/OpenSearch compatibility jobs, and publishes root extras only after the plugin distributions exist.

**Tech Stack:** uv workspace/lock, pytest, Bash, Docker Compose, GitHub Actions, Hatch/uv build, Twine, PyPI Trusted Publishing, Python 3.9-3.12.

---

## Preconditions And File Responsibility Map

Preconditions:

- `plugins/onestep-elasticsearch/pyproject.toml` declares version `0.1.0`, Python
  `>=3.9`, `onestep>=1.7.1`, and `httpx>=0.27`.
- `plugins/onestep-clickhouse/pyproject.toml` declares version `0.1.0`, Python
  `>=3.9`, `onestep>=1.7.1`, and `clickhouse-connect>=0.8`.
- `plugins/onestep-mongodb/pyproject.toml` declares version `0.1.0`, Python
  `>=3.9`, `onestep>=1.7.1`, and `pymongo>=4.13`.
- Each package's non-integration tests and wheel/sdist checks pass without any root
  integration edits.

Shared files owned only by this plan:

- Modify `pyproject.toml`: root extras, `all`/`dev`/`integration`, workspace members, and uv sources.
- Modify `uv.lock`: one regenerated dependency graph after all package metadata is stable.
- Create `tests/test_database_plugin_integration.py`: root metadata, worker-image, workflow, docs, and integration-path contract assertions.
- Modify `scripts/run-reliability-checks.sh` and `tests/test_reliability_checks_script.py`: isolated plugin test paths.
- Modify `docker/worker/Dockerfile`: copy and install all three local plugin packages in the bundled image.
- Modify `docker-compose.integration.yml`: default ClickHouse and MongoDB replica-set services.
- Create `docker/mongodb/init-replica-set.js`: idempotent single-node replica initialization.
- Modify `scripts/setup-integration-env.sh`: readiness and environment exports.
- Modify `scripts/run-integration-tests.sh`: explicit new live-test paths.
- Create `.github/workflows/plugin-elasticsearch.yml`, `.github/workflows/plugin-clickhouse.yml`, and `.github/workflows/plugin-mongodb.yml`: Python/package/live compatibility/publish gates.
- Modify `README.md`, `README.zh-CN.md`, `docs/yaml-task-definition.md`, `skills/onestep/references/connectors.md`, and `CHANGELOG.md`: public install/resources/semantics/release documentation.
- Modify root release metadata only after all three `0.1.0` packages are published.

Do not modify core runtime source, stable exports, runner retry/ack behavior,
control-plane source/protocols, or plugin-local runtime behavior from this plan. If
an integration test finds a plugin bug, return it to that plugin's plan/owner and
resume integration after a plugin-local fix.

### Task 1: Verify Stable Package Handoff

**Files:**
- Read: `plugins/onestep-elasticsearch/**`
- Read: `plugins/onestep-clickhouse/**`
- Read: `plugins/onestep-mongodb/**`

- [ ] **Step 1: Verify package metadata exactly**

Run:

```bash
for package in elasticsearch clickhouse mongodb; do
  uv run --project "plugins/onestep-$package" python -c 'import pathlib, re, sys; text = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"); value = lambda key: re.search(rf"(?m)^{key}\s*=\s*\"([^\"]+)\"", text).group(1); print(value("name"), value("version"), value("requires-python"))' "plugins/onestep-$package/pyproject.toml"
done
```
Expected lines:

```text
onestep-elasticsearch 0.1.0 >=3.9
onestep-clickhouse 0.1.0 >=3.9
onestep-mongodb 0.1.0 >=3.9
```

- [ ] **Step 2: Run every independent non-live suite**

```bash
uv run --project plugins/onestep-elasticsearch --extra test python -m pytest -q plugins/onestep-elasticsearch/tests -m "not integration"
uv run --project plugins/onestep-clickhouse --extra test python -m pytest -q plugins/onestep-clickhouse/tests -m "not integration"
uv run --project plugins/onestep-mongodb --extra test python -m pytest -q plugins/onestep-mongodb/tests -m "not integration"
```

Expected: all three commands PASS. Stop integration and return failures to the
owning plugin track; do not patch plugin-local runtime files here.

- [ ] **Step 3: Build all three package distributions**

```bash
uv build plugins/onestep-elasticsearch --out-dir /tmp/onestep-db-plugin-dist/elasticsearch --sdist --wheel --clear
uv build plugins/onestep-clickhouse --out-dir /tmp/onestep-db-plugin-dist/clickhouse --sdist --wheel --clear
uv build plugins/onestep-mongodb --out-dir /tmp/onestep-db-plugin-dist/mongodb --sdist --wheel --clear
uvx twine check /tmp/onestep-db-plugin-dist/*/*
```

Expected: six artifacts are created and every artifact reports `PASSED`.

### Task 2: Add Root Workspace, Extras, Sources, And One Lock Update

**Files:**
- Create: `tests/test_database_plugin_integration.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`

- [ ] **Step 1: Write the failing root metadata contract**

Create `tests/test_database_plugin_integration.py`:

```python
from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


def test_root_metadata_integrates_database_plugins() -> None:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    expected = {
        "elasticsearch": "onestep-elasticsearch>=0.1.0",
        "clickhouse": "onestep-clickhouse>=0.1.0",
        "mongodb": "onestep-mongodb>=0.1.0",
    }
    for extra, dependency in expected.items():
        assert re.search(rf"(?ms)^{extra} = \[\s*\"{re.escape(dependency)}\",?\s*\]", text)
        for aggregate in ("all", "dev", "integration"):
            section = re.search(rf"(?ms)^{aggregate} = \[(.*?)^\]", text)
            assert section is not None and f'"{dependency}"' in section.group(1)
    for extra in expected:
        package = f"onestep-{extra}"
        assert f'"plugins/{package}"' in text
        assert f"{package} = {{ workspace = true }}" in text
```

- [ ] **Step 2: Run and verify the missing extras fail**

```bash
uv run --extra test python -m pytest -q tests/test_database_plugin_integration.py::test_root_metadata_integrates_database_plugins
```

Expected: FAIL on the missing `elasticsearch = ["onestep-elasticsearch>=0.1.0"]`
extra assertion.

- [ ] **Step 3: Add exact root dependency entries**

Add these extras to `[project.optional-dependencies]`:

```toml
elasticsearch = ["onestep-elasticsearch>=0.1.0"]
clickhouse = ["onestep-clickhouse>=0.1.0"]
mongodb = ["onestep-mongodb>=0.1.0"]
```

Add all three dependency strings to `integration`, `all`, and `dev`. Add these
members:

```toml
"plugins/onestep-elasticsearch",
"plugins/onestep-clickhouse",
"plugins/onestep-mongodb",
```

Add these sources:

```toml
onestep-elasticsearch = { workspace = true }
onestep-clickhouse = { workspace = true }
onestep-mongodb = { workspace = true }
```

- [ ] **Step 4: Regenerate and verify the lock exactly once**

```bash
uv lock
uv lock --check
```

Expected: both commands exit 0; `uv.lock` contains the three local packages and
their `httpx`, `clickhouse-connect`, and `pymongo` dependency graphs.

- [ ] **Step 5: Run the metadata test and workspace import smoke**

```bash
uv run --all-packages --extra test python -m pytest -q tests/test_database_plugin_integration.py::test_root_metadata_integrates_database_plugins
uv run --all-packages python -c 'import onestep_elasticsearch, onestep_clickhouse, onestep_mongodb; print("database plugins import")'
```

Expected: test PASS and output `database plugins import`.

- [ ] **Step 6: Commit root metadata and lock**

```bash
git add pyproject.toml uv.lock tests/test_database_plugin_integration.py
git commit -m "build: integrate database plugin packages"
```

### Task 3: Add Isolated Reliability Gates

**Files:**
- Modify: `tests/test_reliability_checks_script.py`
- Modify: `scripts/run-reliability-checks.sh`

- [ ] **Step 1: Extend the failing reliability-script assertion**

Add these strings to the existing plugin tuple in
`test_reliability_check_script_runs_plugin_suites_in_isolated_processes`:

```python
"plugins/onestep-clickhouse/tests",
"plugins/onestep-elasticsearch/tests",
"plugins/onestep-mongodb/tests",
```

- [ ] **Step 2: Run and verify all three paths are missing**

```bash
uv run --extra test python -m pytest -q tests/test_reliability_checks_script.py
```

Expected: FAIL on the first new plugin path assertion.

- [ ] **Step 3: Add sorted plugin paths to the isolated process array**

Insert into `plugin_paths` in `scripts/run-reliability-checks.sh`:

```bash
  "plugins/onestep-clickhouse/tests"
  "plugins/onestep-elasticsearch/tests"
  "plugins/onestep-mongodb/tests"
```

Keep Kafka's Python-3.10 special path unchanged. The three new packages support
Python 3.9 and run through the normal isolated loop.

- [ ] **Step 4: Validate Bash and focused tests**

```bash
bash -n scripts/run-reliability-checks.sh
uv run --extra test python -m pytest -q tests/test_reliability_checks_script.py
```

Expected: Bash exits 0 and both tests pass.

- [ ] **Step 5: Commit reliability wiring**

```bash
git add scripts/run-reliability-checks.sh tests/test_reliability_checks_script.py
git commit -m "test: include database plugins in reliability checks"
```

### Task 4: Add The Three Plugins To The Bundled Worker Image

**Files:**
- Modify: `tests/test_database_plugin_integration.py`
- Modify: `docker/worker/Dockerfile`

- [ ] **Step 1: Write a failing Dockerfile contract test**

Append:

```python
def test_bundled_worker_copies_and_installs_database_plugins() -> None:
    text = (ROOT / "docker" / "worker" / "Dockerfile").read_text(encoding="utf-8")
    for package, module in (
        ("onestep-elasticsearch", "onestep_elasticsearch"),
        ("onestep-clickhouse", "onestep_clickhouse"),
        ("onestep-mongodb", "onestep_mongodb"),
    ):
        assert f"COPY plugins/{package}/pyproject.toml plugins/{package}/README.md /tmp/onestep/plugins/{package}/" in text
        assert f"COPY plugins/{package}/src/{module} /tmp/onestep/plugins/{package}/src/{module}" in text
        assert f"/tmp/onestep/plugins/{package}" in text
```

- [ ] **Step 2: Run and verify Dockerfile assertions fail**

```bash
uv run --extra test python -m pytest -q tests/test_database_plugin_integration.py::test_bundled_worker_copies_and_installs_database_plugins
```

Expected: FAIL for `onestep-elasticsearch`.

- [ ] **Step 3: Add exact copy and install lines**

Add for each package, following the existing local plugin blocks:

```dockerfile
COPY plugins/onestep-elasticsearch/pyproject.toml plugins/onestep-elasticsearch/README.md /tmp/onestep/plugins/onestep-elasticsearch/
COPY plugins/onestep-elasticsearch/src/onestep_elasticsearch /tmp/onestep/plugins/onestep-elasticsearch/src/onestep_elasticsearch
COPY plugins/onestep-clickhouse/pyproject.toml plugins/onestep-clickhouse/README.md /tmp/onestep/plugins/onestep-clickhouse/
COPY plugins/onestep-clickhouse/src/onestep_clickhouse /tmp/onestep/plugins/onestep-clickhouse/src/onestep_clickhouse
COPY plugins/onestep-mongodb/pyproject.toml plugins/onestep-mongodb/README.md /tmp/onestep/plugins/onestep-mongodb/
COPY plugins/onestep-mongodb/src/onestep_mongodb /tmp/onestep/plugins/onestep-mongodb/src/onestep_mongodb
```

Add these three local paths to the `pip install` command:

```dockerfile
        /tmp/onestep/plugins/onestep-elasticsearch \
        /tmp/onestep/plugins/onestep-clickhouse \
        /tmp/onestep/plugins/onestep-mongodb \
```

- [ ] **Step 4: Run the contract and worker build smoke**

```bash
uv run --extra test python -m pytest -q tests/test_database_plugin_integration.py::test_bundled_worker_copies_and_installs_database_plugins
docker build -f docker/worker/Dockerfile -t onestep-worker:database-plugins .
docker run --rm --entrypoint python onestep-worker:database-plugins -c 'import onestep_elasticsearch, onestep_clickhouse, onestep_mongodb; print("bundled")'
```

Expected: test passes, image builds, and container prints `bundled`.

- [ ] **Step 5: Commit worker integration**

```bash
git add docker/worker/Dockerfile tests/test_database_plugin_integration.py
git commit -m "build: bundle database connector plugins"
```

### Task 5: Add Default ClickHouse And MongoDB Replica-Set Integration Services

**Files:**
- Modify: `docker-compose.integration.yml`
- Create: `docker/mongodb/init-replica-set.js`
- Modify: `scripts/setup-integration-env.sh`
- Modify: `scripts/run-integration-tests.sh`
- Modify: `tests/test_database_plugin_integration.py`

- [ ] **Step 1: Add failing service/path assertions**

Append:

```python
def test_integration_harness_contains_database_services_and_tests() -> None:
    compose = (ROOT / "docker-compose.integration.yml").read_text(encoding="utf-8")
    setup = (ROOT / "scripts" / "setup-integration-env.sh").read_text(encoding="utf-8")
    runner = (ROOT / "scripts" / "run-integration-tests.sh").read_text(encoding="utf-8")
    assert "clickhouse:" in compose and "mongo:" in compose
    assert "ONESTEP_CLICKHOUSE_DSN" in setup and "ONESTEP_MONGODB_URI" in setup
    assert "plugins/onestep-clickhouse/tests/integration" in runner
    assert "plugins/onestep-mongodb/tests/integration" in runner
    assert "plugins/onestep-elasticsearch/tests/integration" not in runner
```

The last assertion preserves the separate search compatibility matrix instead of
starting Elasticsearch and OpenSearch simultaneously in the default stack.

- [ ] **Step 2: Run and verify service assertions fail**

```bash
uv run --extra test python -m pytest -q tests/test_database_plugin_integration.py::test_integration_harness_contains_database_services_and_tests
```

Expected: FAIL because ClickHouse/MongoDB are absent.

- [ ] **Step 3: Add exact Compose services**

Append under `services`:

```yaml
  clickhouse:
    image: clickhouse/clickhouse-server:25.3
    container_name: onestep-clickhouse
    environment:
      CLICKHOUSE_DB: onestep
      CLICKHOUSE_USER: default
      CLICKHOUSE_PASSWORD: ""
    ports:
      - "8123:8123"
    healthcheck:
      test: ["CMD", "wget", "--spider", "-q", "http://127.0.0.1:8123/ping"]
      interval: 5s
      timeout: 5s
      retries: 30

  mongo:
    image: mongo:8.0
    container_name: onestep-mongodb
    command: ["mongod", "--replSet", "rs0", "--bind_ip_all"]
    ports:
      - "27017:27017"
    healthcheck:
      test: ["CMD", "mongosh", "--quiet", "--eval", "db.adminCommand('ping').ok"]
      interval: 5s
      timeout: 5s
      retries: 30
```

Create `docker/mongodb/init-replica-set.js`:

```javascript
try {
  rs.status();
} catch (error) {
  rs.initiate({ _id: "rs0", members: [{ _id: 0, host: "127.0.0.1:27017" }] });
}
```

- [ ] **Step 4: Add readiness and exported variables**

In `setup-integration-env.sh`, define:

```bash
CLICKHOUSE_DSN="${ONESTEP_CLICKHOUSE_DSN:-http://default:@127.0.0.1:8123/onestep}"
MONGODB_URI="${ONESTEP_MONGODB_URI:-mongodb://127.0.0.1:27017/onestep?replicaSet=rs0}"
```

After Compose health checks, run:

```bash
docker exec onestep-mongodb mongosh --quiet /dev/stdin < "$ROOT_DIR/docker/mongodb/init-replica-set.js"
wait_for_url "http://127.0.0.1:8123/ping" "ClickHouse"
```

Add this exact readiness function and call it after replica initialization:

```bash
wait_for_mongodb() {
  MONGODB_URI="$MONGODB_URI" "$PYTHON_BIN" - <<'PY'
import asyncio
import os

from pymongo import AsyncMongoClient


async def main():
    last_error = None
    for _ in range(60):
        client = AsyncMongoClient(os.environ["MONGODB_URI"], serverSelectionTimeoutMS=2000)
        try:
            await client.admin.command("ping")
            await client.close()
            return
        except Exception as exc:
            last_error = exc
            await client.close()
            await asyncio.sleep(2)
    raise RuntimeError(f"Timed out waiting for MongoDB replica set: {last_error}")


asyncio.run(main())
PY
}

wait_for_mongodb
```

Export:

```bash
export ONESTEP_CLICKHOUSE_DSN="$CLICKHOUSE_DSN"
export ONESTEP_MONGODB_URI="$MONGODB_URI"
```

- [ ] **Step 5: Add explicit live test paths**

Add to the loop in `run-integration-tests.sh`:

```bash
  plugins/onestep-clickhouse/tests/integration \
  plugins/onestep-mongodb/tests/integration \
```

- [ ] **Step 6: Validate configuration and run the two new live suites**

```bash
docker compose -f docker-compose.integration.yml config --quiet
bash -n scripts/setup-integration-env.sh scripts/run-integration-tests.sh
uv run --extra integration ./scripts/run-integration-tests.sh plugins/onestep-clickhouse/tests/integration plugins/onestep-mongodb/tests/integration
```

Expected: Compose/Bash validation exits 0; Mongo initializes `rs0`; ClickHouse and
MongoDB live tests pass.

- [ ] **Step 7: Commit default live-service integration**

```bash
git add docker-compose.integration.yml docker/mongodb/init-replica-set.js scripts/setup-integration-env.sh scripts/run-integration-tests.sh tests/test_database_plugin_integration.py
git commit -m "test: add clickhouse and mongodb integration services"
```

### Task 6: Add Independent Test, Compatibility, Build, And Publish Workflows

**Files:**
- Create: `.github/workflows/plugin-elasticsearch.yml`
- Create: `.github/workflows/plugin-clickhouse.yml`
- Create: `.github/workflows/plugin-mongodb.yml`
- Modify: `tests/test_database_plugin_integration.py`

- [ ] **Step 1: Write failing workflow contract assertions**

Append:

```python
def test_database_plugin_workflows_gate_build_live_and_publish() -> None:
    expected = {
        "elasticsearch": ("onestep-elasticsearch", "ELASTICSEARCH_PYPI_API_TOKEN"),
        "clickhouse": ("onestep-clickhouse", "CLICKHOUSE_PYPI_API_TOKEN"),
        "mongodb": ("onestep-mongodb", "MONGODB_PYPI_API_TOKEN"),
    }
    for slug, (package, secret) in expected.items():
        text = (ROOT / ".github" / "workflows" / f"plugin-{slug}.yml").read_text(encoding="utf-8")
        assert f"PLUGIN_PACKAGE: {package}" in text
        assert 'python-version: ["3.9", "3.10", "3.11", "3.12"]' in text
        assert "--sdist --wheel" in text and "twine check" in text
        assert "live-compatibility" in text
        assert "id-token: write" in text and secret in text
        assert "needs.test.result == 'success'" in text
        assert "needs.live-compatibility.result == 'success'" in text
```

- [ ] **Step 2: Run and verify workflow files are absent**

```bash
uv run --extra test python -m pytest -q tests/test_database_plugin_integration.py::test_database_plugin_workflows_gate_build_live_and_publish
```

Expected: FAIL with `FileNotFoundError`.

- [ ] **Step 3: Create each workflow from the established publish template**

Copy `.github/workflows/plugin-postgres.yml` three times, then make these exact
substitutions throughout each copy:

```text
plugin-elasticsearch.yml: PostgreSQL -> Elasticsearch/OpenSearch; postgres -> elasticsearch; onestep-postgres -> onestep-elasticsearch; POSTGRES_PYPI_API_TOKEN -> ELASTICSEARCH_PYPI_API_TOKEN
plugin-clickhouse.yml: PostgreSQL -> ClickHouse; postgres -> clickhouse; onestep-postgres -> onestep-clickhouse; POSTGRES_PYPI_API_TOKEN -> CLICKHOUSE_PYPI_API_TOKEN
plugin-mongodb.yml: PostgreSQL -> MongoDB; postgres -> mongodb; onestep-postgres -> onestep-mongodb; POSTGRES_PYPI_API_TOKEN -> MONGODB_PYPI_API_TOKEN
```

In every test job, use:

```yaml
      - name: Run plugin tests
        run: uv run --all-packages python -m pytest -q "$PLUGIN_PATH/tests" -m "not integration"
```

Keep Python 3.9-3.12, wheel+sdist, Twine, version detection, published-onestep
verification, trusted-publish permission, and token fallbacks from the source
workflow. Add `uv.lock` and the workflow itself to push/pull-request path filters.

- [ ] **Step 4: Add an Elasticsearch/OpenSearch live compatibility matrix**

Add a job named exactly `live-compatibility` with matrix rows:

```yaml
matrix:
  include:
    - { name: elasticsearch-8, image: "docker.elastic.co/elasticsearch/elasticsearch:8.19.0", distribution: elasticsearch }
    - { name: elasticsearch-9, image: "docker.elastic.co/elasticsearch/elasticsearch:9.1.0", distribution: elasticsearch }
    - { name: opensearch-2, image: "opensearchproject/opensearch:2", distribution: opensearch }
    - { name: opensearch-3, image: "opensearchproject/opensearch:3", distribution: opensearch }
```

Use this complete job after substituting the four matrix rows above:

```yaml
  live-compatibility:
    name: Live ${{ matrix.name }}
    needs: test
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        include:
          - { name: elasticsearch-8, image: "docker.elastic.co/elasticsearch/elasticsearch:8.19.0", distribution: elasticsearch }
          - { name: elasticsearch-9, image: "docker.elastic.co/elasticsearch/elasticsearch:9.1.0", distribution: elasticsearch }
          - { name: opensearch-2, image: "opensearchproject/opensearch:2", distribution: opensearch }
          - { name: opensearch-3, image: "opensearchproject/opensearch:3", distribution: opensearch }
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - uses: astral-sh/setup-uv@v5
      - name: Start one search distribution
        env:
          IMAGE: ${{ matrix.image }}
          DISTRIBUTION: ${{ matrix.distribution }}
        run: |
          set -euo pipefail
          if [ "$DISTRIBUTION" = elasticsearch ]; then
            docker run -d --name search -p 9200:9200 -e discovery.type=single-node -e xpack.security.enabled=false -e ES_JAVA_OPTS='-Xms512m -Xmx512m' "$IMAGE"
          else
            docker run -d --name search -p 9200:9200 -e discovery.type=single-node -e plugins.security.disabled=true -e OPENSEARCH_JAVA_OPTS='-Xms512m -Xmx512m' "$IMAGE"
          fi
          for attempt in $(seq 1 90); do curl -fsS http://127.0.0.1:9200 >/dev/null && exit 0; sleep 2; done
          docker logs search
          exit 1
      - name: Install test dependencies
        run: uv sync --frozen --all-packages --extra test
      - name: Run common live suite
        env:
          ONESTEP_ELASTICSEARCH_URL: ${{ matrix.distribution == 'elasticsearch' && 'http://127.0.0.1:9200' || '' }}
          ONESTEP_OPENSEARCH_URL: ${{ matrix.distribution == 'opensearch' && 'http://127.0.0.1:9200' || '' }}
        run: uv run --all-packages python -m pytest -q plugins/onestep-elasticsearch/tests/integration -m integration
```

Change `publish-pypi.needs` to `[test, live-compatibility, detect-version]` and its
condition to:

```yaml
    if: >-
      ${{
        needs.test.result == 'success' &&
        needs.live-compatibility.result == 'success' &&
        needs.detect-version.outputs.should_publish == 'true'
      }}
```

- [ ] **Step 5: Add ClickHouse and MongoDB live compatibility jobs**

Add this complete ClickHouse job:

```yaml
  live-compatibility:
    name: Live ClickHouse ${{ matrix.version }}
    needs: test
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        version: ["24.8", "25.3"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - uses: astral-sh/setup-uv@v5
      - name: Start ClickHouse
        run: |
          docker run -d --name clickhouse -p 8123:8123 -e CLICKHOUSE_DB=onestep "clickhouse/clickhouse-server:${{ matrix.version }}"
          for attempt in $(seq 1 60); do curl -fsS http://127.0.0.1:8123/ping >/dev/null && exit 0; sleep 2; done
          docker logs clickhouse
          exit 1
      - name: Install test dependencies
        run: uv sync --frozen --all-packages --extra test
      - name: Run live suite
        env: { ONESTEP_CLICKHOUSE_DSN: "http://default:@127.0.0.1:8123/onestep" }
        run: uv run --all-packages python -m pytest -q plugins/onestep-clickhouse/tests/integration -m integration
```

Add this complete MongoDB job:

```yaml
  live-compatibility:
    name: Live MongoDB replica set
    needs: test
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - uses: astral-sh/setup-uv@v5
      - name: Start MongoDB replica set
        run: |
          docker run -d --name mongodb -p 27017:27017 mongo:8.0 mongod --replSet rs0 --bind_ip_all
          for attempt in $(seq 1 60); do docker exec mongodb mongosh --quiet --eval 'db.adminCommand("ping").ok' && break; sleep 2; done
          docker exec mongodb mongosh --quiet --eval 'rs.initiate({_id:"rs0",members:[{_id:0,host:"127.0.0.1:27017"}]})'
          for attempt in $(seq 1 60); do docker exec mongodb mongosh --quiet --eval 'rs.status().myState == 1' | grep -q true && exit 0; sleep 2; done
          docker logs mongodb
          exit 1
      - name: Install test dependencies
        run: uv sync --frozen --all-packages --extra test
      - name: Run live suite
        env: { ONESTEP_MONGODB_URI: "mongodb://127.0.0.1:27017/onestep?replicaSet=rs0" }
        run: uv run --all-packages python -m pytest -q plugins/onestep-mongodb/tests/integration -m integration
```

Apply the same publish `needs` and `if` block from Step 4 to both workflows. Forced
MongoDB primary stepdown is not a publish gate; expose it only through a manual
workflow-dispatch input.

- [ ] **Step 6: Validate workflow syntax and contract**

```bash
uv run --extra test python -m pytest -q tests/test_database_plugin_integration.py::test_database_plugin_workflows_gate_build_live_and_publish
for workflow in .github/workflows/plugin-elasticsearch.yml .github/workflows/plugin-clickhouse.yml .github/workflows/plugin-mongodb.yml; do
  ruby -e 'require "yaml"; YAML.load_file(ARGV[0], aliases: true); puts ARGV[0]' "$workflow"
done
```

Expected: contract test passes and Ruby prints all three paths without a YAML error.

- [ ] **Step 7: Commit workflow gates**

```bash
git add .github/workflows/plugin-elasticsearch.yml .github/workflows/plugin-clickhouse.yml .github/workflows/plugin-mongodb.yml tests/test_database_plugin_integration.py
git commit -m "ci: add database plugin release workflows"
```

### Task 7: Update Public Docs, Agent Guidance, And Changelog

**Files:**
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `docs/yaml-task-definition.md`
- Modify: `skills/onestep/references/connectors.md`
- Modify: `CHANGELOG.md`
- Modify: `tests/test_database_plugin_integration.py`

- [ ] **Step 1: Add failing documentation contract assertions**

Append:

```python
def test_public_docs_name_database_plugin_resources_and_semantics() -> None:
    files = [ROOT / "README.md", ROOT / "docs" / "yaml-task-definition.md", ROOT / "skills" / "onestep" / "references" / "connectors.md", ROOT / "CHANGELOG.md"]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in files)
    for value in ("onestep-elasticsearch", "elasticsearch_bulk_sink", "onestep-clickhouse", "clickhouse_table_sink", "onestep-mongodb", "mongodb_polling", "mongodb_change_stream", "mongodb_collection_sink"):
        assert value in combined
    assert "full_document: updateLookup" in combined
    assert "durable" in combined and "UNCERTAIN" in combined
```

- [ ] **Step 2: Run and verify missing documentation names fail**

```bash
uv run --extra test python -m pytest -q tests/test_database_plugin_integration.py::test_public_docs_name_database_plugin_resources_and_semantics
```

Expected: FAIL for `onestep-elasticsearch`.

- [ ] **Step 3: Update root README files**

Add `pip install 'onestep[elasticsearch]'`, `[clickhouse]`, and `[mongodb]` to
optional-extra examples; add all three packages/resources to connector/capability
tables; mention Elasticsearch/OpenSearch common REST scope, ClickHouse table
inserts, MongoDB polling/change streams/sink, and at-least-once duplicates. Mirror
the same factual surface in `README.zh-CN.md` without changing unrelated prose.

- [ ] **Step 4: Update strict YAML and onestep skill references**

In `docs/yaml-task-definition.md`, add the approved strict YAML examples from the
design and list every resource field/default. In
`skills/onestep/references/connectors.md`, add concise Elasticsearch/OpenSearch,
ClickHouse, and MongoDB sections. State explicitly:

```markdown
All three bulk sinks accept one mapping or a non-empty sequence of mappings and
await every backend chunk acknowledgement. A retry can repeat committed chunks;
use stable IDs/keys or backend dedup-aware schema design when duplicates matter.

MongoDB polling/change streams may use in-memory state for development. Production
restart guarantees require an explicit durable `state` cursor store. Change streams
emit raw events and default to `full_document: updateLookup`.
```

- [ ] **Step 5: Add unreleased core metadata and three plugin entries**

At the top of `CHANGELOG.md`, add:

```markdown
## Unreleased

- Adds `elasticsearch`, `clickhouse`, and `mongodb` optional extras for the three independently published connector plugins.
- Bundles the three plugins in the worker image and adds isolated reliability/live compatibility gates.
- Does not change core runtime, delivery, retry, reporter, or control-plane behavior.

## onestep-elasticsearch 0.1.0

- Adds the common Elasticsearch/OpenSearch HTTP connector and acknowledged bulk sink.
- Registers `elasticsearch` and `elasticsearch_bulk_sink` with strict catalog metadata.
- Covers Elasticsearch 8/9 and OpenSearch 2/3 before publishing.

## onestep-clickhouse 0.1.0

- Adds acknowledged async ClickHouse table inserts with explicit mapping-or-sequence batching.
- Registers `clickhouse` and `clickhouse_table_sink` with strict catalog metadata.

## onestep-mongodb 0.1.0

- Adds deterministic polling, raw-event resumable change streams using `updateLookup`, and insert/upsert sinks.
- Requires explicit durable cursor state for production restart guarantees while retaining in-memory development defaults.
```

- [ ] **Step 6: Run documentation tests and strict examples**

```bash
uv run --all-packages --extra test python -m pytest -q tests/test_database_plugin_integration.py
uv run --all-packages onestep catalog --json > /tmp/onestep-database-plugin-catalog.json
python -m json.tool /tmp/onestep-database-plugin-catalog.json >/dev/null
git diff --check
```

Expected: integration contract tests pass, catalog JSON is valid and includes all
eight new resource types, and diff check is clean.

- [ ] **Step 7: Commit documentation integration**

```bash
git add README.md README.zh-CN.md docs/yaml-task-definition.md skills/onestep/references/connectors.md CHANGELOG.md tests/test_database_plugin_integration.py
git commit -m "docs: document database connector plugins"
```

### Task 8: Run Full Gates And Publish In Dependency Order

**Files:**
- Modify after plugin publication: `pyproject.toml`
- Modify after plugin publication: `CHANGELOG.md`
- Modify after plugin publication: `uv.lock`

- [ ] **Step 1: Run all non-live reliability and package gates**

```bash
uv lock --check
uv run --all-packages --extra test python -m pytest -q -m "not integration"
./scripts/run-reliability-checks.sh
uv build --package onestep-elasticsearch --out-dir /tmp/release/elasticsearch --sdist --wheel --clear
uv build --package onestep-clickhouse --out-dir /tmp/release/clickhouse --sdist --wheel --clear
uv build --package onestep-mongodb --out-dir /tmp/release/mongodb --sdist --wheel --clear
uvx twine check /tmp/release/*/*
```

Expected: lock current; core and every isolated plugin suite pass; six plugin
artifacts pass metadata validation.

- [ ] **Step 2: Run required live gates**

Trigger or run the workflow jobs for Elasticsearch 8.x, Elasticsearch 9.x,
OpenSearch 2.x, OpenSearch 3.x, ClickHouse LTS/current, and MongoDB replica set.
Run the default integration stack once for ClickHouse/MongoDB and existing services.

Expected: every required matrix cell and the default stack pass. Do not publish a
plugin whose affected live gate failed.

- [ ] **Step 3: Publish the three plugin packages at `0.1.0`**

Use each plugin workflow's trusted publish job after its tests/live matrix pass.
Verify availability without installing root extras:

```bash
python -c 'import json, urllib.request; names=("onestep-elasticsearch", "onestep-clickhouse", "onestep-mongodb"); [print(name, "0.1.0" in json.load(urllib.request.urlopen(f"https://pypi.org/pypi/{name}/json"))["releases"]) for name in names]'
```

Expected: all three lines end in `True`.

- [ ] **Step 4: Prepare the root extras release only after publication**

Change root `project.version` from `1.7.2` to `1.8.0`, replace `## Unreleased` with
`## 1.8.0`, regenerate `uv.lock`, and run:

```bash
uv lock --check
uv build --package onestep --out-dir /tmp/release/core --sdist --wheel --clear
uvx twine check /tmp/release/core/*
python -m pip install --dry-run 'onestep[elasticsearch,clickhouse,mongodb]==1.8.0' --find-links /tmp/release/core
```

Expected: core artifacts pass and the dry run resolves the three published
`0.1.0` packages. This is a metadata/docs release; stable runtime APIs remain
unchanged.

- [ ] **Step 5: Commit the release metadata**

```bash
git add pyproject.toml uv.lock CHANGELOG.md
git commit -m "chore: prepare onestep 1.8.0"
```

- [ ] **Step 6: Final clean-tree and ownership audit**

```bash
git diff --check HEAD~1 HEAD
git status --short
git log --oneline --max-count=12
```

Expected: no uncommitted changes, no core runtime/control-plane files changed, and
shared edits are confined to the ownership map above.

## Plan Completion Gate

The integration is complete only after plugin-local packages were stable before
shared edits, every plugin suite ran in isolation, required live matrices passed,
all three `0.1.0` packages became available, and the root extras/bundled-worker
release was prepared afterward. No `onestep-control-plane` release is part of this
sequence.
