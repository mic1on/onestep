# Feishu Bitable Relation Resolver Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Add declarative Feishu Bitable relation resolution so table sinks can map one or many business keys to related record IDs, leave missing values empty, or create missing records and write their IDs back.

**Architecture:** Extend the existing FeishuBitableTableSink with normalized immutable relation configurations and a write-time resolution phase. Keep YAML/catalog parsing in resources.py, reuse the connector's existing search/create methods and ConnectorOperationError model, and serialize only safe relation metadata in descriptors.

**Tech Stack:** Python 3.10+, asyncio, onestep Source/Sink APIs, Feishu Bitable HTTP API, pytest, YAML resource registry.

---

### Task 1: Relation configuration contract

**Files:**
- Modify: plugins/onestep-feishu-bitable/src/onestep_feishu_bitable/connector.py
- Test: plugins/onestep-feishu-bitable/tests/test_feishu_bitable_connector.py

- [ ] **Step 1: Write failing Python API validation tests**

Add tests that construct a sink with a valid relation mapping and assert its normalized fields. Add parametrized invalid cases for an empty relations mapping, unknown relation fields, missing table_id/key, invalid on_missing, create_fields outside create, create_fields containing key, and conflicts with match_fields.

~~~python
sink = connector.table_sink(
    app_token="project-app",
    table_id="projects",
    mode="upsert",
    match_fields=["project_id"],
    relations={
        "companies": {
            "from": "company_names",
            "table_id": "companies",
            "key": "name",
            "on_missing": "create",
        }
    },
)
assert sink.relations[0].target_field == "companies"
~~~

- [ ] **Step 2: Run validation tests and verify they fail**

Run:

~~~bash
uv run --all-packages pytest -q plugins/onestep-feishu-bitable/tests/test_feishu_bitable_connector.py -k relation_config
~~~

Expected: failures because table_sink does not accept relations.

- [ ] **Step 3: Add normalized immutable relation configuration**

Add _FeishuRelationConfig and _normalize_relations(). Extend FeishuBitableConnector.table_sink() and FeishuBitableTableSink.__init__() with relations: Mapping[str, Mapping[str, Any]] | None. Normalize defaults, reject conflicts, copy create_fields, and leave relations as an empty tuple when omitted.

- [ ] **Step 4: Run validation tests and existing sink tests**

Run:

~~~bash
uv run --all-packages pytest -q plugins/onestep-feishu-bitable/tests/test_feishu_bitable_connector.py -k "relation_config or table_sink"
~~~

Expected: all selected tests pass.

### Task 2: Single and multi-value relation resolution

**Files:**
- Modify: plugins/onestep-feishu-bitable/src/onestep_feishu_bitable/connector.py
- Test: plugins/onestep-feishu-bitable/tests/test_feishu_bitable_connector.py

- [ ] **Step 1: Write failing resolution behavior tests**

Cover scalar and three-value inputs, first-seen deduplication, empty inputs, invalid input types, duplicate related records, error missing behavior, empty partial matches, shared from fields, input immutability, and removal of consumed from aliases.

~~~python
await sink.send(
    Envelope(
        body={
            "project_id": "P-001",
            "company_names": ["A", "B", "A", "C"],
        }
    )
)
assert created_project["fields"]["companies"] == ["rec-a", "rec-b", "rec-c"]
assert "company_names" not in created_project["fields"]
~~~

- [ ] **Step 2: Run focused tests and verify they fail**

Run:

~~~bash
uv run --all-packages pytest -q plugins/onestep-feishu-bitable/tests/test_feishu_bitable_connector.py -k relation_resolution
~~~

Expected: failures because send() still forwards fields unchanged.

- [ ] **Step 3: Implement relation resolution before target matching**

Add helpers that normalize values to ordered unique strings, query with page_size=2, apply error/empty behavior, and return a copied fields mapping with source aliases removed only after all relations resolve. Preserve ConnectorOperationError and wrap payload errors as permanent SEND failures through the existing send() boundary.

- [ ] **Step 4: Run focused resolution and regression tests**

Run:

~~~bash
uv run --all-packages pytest -q plugins/onestep-feishu-bitable/tests/test_feishu_bitable_connector.py -k "relation_resolution or table_sink"
~~~

Expected: all selected tests pass.

### Task 3: Create-on-missing, cross-Base routing, and single-flight

**Files:**
- Modify: plugins/onestep-feishu-bitable/src/onestep_feishu_bitable/connector.py
- Test: plugins/onestep-feishu-bitable/tests/test_feishu_bitable_connector.py

- [ ] **Step 1: Write failing create behavior tests**

Cover creation of one and several missing records, merging found and created IDs in input order, static create_fields, relation app_token overrides, partial create failures that do not write the project, retry after a prior partial create, and two concurrent sends that create the same business value once.

~~~python
await asyncio.gather(
    sink.send(Envelope(body={"project_id": "P-1", "company_names": ["A"]})),
    sink.send(Envelope(body={"project_id": "P-2", "company_names": ["A"]})),
)
assert created_company_names == ["A"]
~~~

- [ ] **Step 2: Run focused tests and verify they fail**

Run:

~~~bash
uv run --all-packages pytest -q plugins/onestep-feishu-bitable/tests/test_feishu_bitable_connector.py -k relation_create
~~~

Expected: failures because create-on-missing and relation locks do not exist.

- [ ] **Step 3: Implement create-on-missing and per-value locks**

Add lazy asyncio lock storage on each sink. For create, acquire a lock keyed by app_token/table_id/key/value, query again inside the lock, create only when still missing, extract the created record ID from the Feishu response, and remove idle lock entries safely. Use the relation app_token for search/create and the sink user_id_type consistently.

- [ ] **Step 4: Run create, resolution, and transport tests**

Run:

~~~bash
uv run --all-packages pytest -q plugins/onestep-feishu-bitable/tests/test_feishu_bitable_connector.py -k "relation_create or relation_resolution or http_error"
~~~

Expected: all selected tests pass.

### Task 4: YAML, catalog, and descriptor integration

**Files:**
- Modify: plugins/onestep-feishu-bitable/src/onestep_feishu_bitable/resources.py
- Modify: plugins/onestep-feishu-bitable/src/onestep_feishu_bitable/connector.py
- Test: plugins/onestep-feishu-bitable/tests/test_feishu_bitable_connector.py
- Test: plugins/onestep-feishu-bitable/tests/test_feishu_bitable_plugin.py

- [ ] **Step 1: Write failing strict YAML, catalog, and descriptor tests**

Add a valid YAML sink with multiple relations and invalid nested cases matching the Python contract. Assert the resource catalog exposes relations as mapping. Assert descriptor output contains safe relation metadata but no sink or relation app token and no create_fields values.

- [ ] **Step 2: Run focused integration tests and verify they fail**

Run:

~~~bash
uv run --all-packages pytest -q plugins/onestep-feishu-bitable/tests -k "yaml and relation or catalog or descriptor"
~~~

Expected: failures because relations is not an allowed resource field.

- [ ] **Step 3: Implement resource and descriptor support**

Add relations to allowed fields and catalog, validate nested relation mappings in strict mode using the same field names and policies, pass relations to table_sink(), and serialize only target_field/from/table_id/key/on_missing/create_field_names/uses_custom_app_token.

- [ ] **Step 4: Run plugin tests**

Run:

~~~bash
uv run --all-packages pytest -q plugins/onestep-feishu-bitable/tests
~~~

Expected: the entire plugin suite passes.

### Task 5: Documentation, release metadata, and final validation

**Files:**
- Modify: docs/broker/feishu-bitable.md
- Modify: plugins/onestep-feishu-bitable/README.md
- Modify: plugins/onestep-feishu-bitable/pyproject.toml
- Modify: CHANGELOG.md or the repository's current plugin release metadata file if required by existing convention

- [ ] **Step 1: Document the supported configuration and operational limits**

Add the enterprise/project YAML example, scalar and list handler payloads, error/empty/create semantics, key uniqueness requirement, cross-Base app_token option, and the multi-process create race limitation. Keep README to a minimal example and link to the full documentation.

- [ ] **Step 2: Update plugin release metadata using repository conventions**

Inspect the latest Feishu plugin release commit and bump the plugin minor version because this adds a backward-compatible public capability. Update only the metadata files that convention requires.

- [ ] **Step 3: Run formatting and focused full validation**

Run:

~~~bash
uv run --all-packages pytest -q plugins/onestep-feishu-bitable/tests
uv build --package onestep-feishu-bitable --out-dir dist/plugin --sdist --wheel --clear
uvx twine check dist/plugin/*
git diff --check
~~~

Expected: tests pass, wheel and sdist build, twine reports both distributions valid, and git diff has no whitespace errors.

- [ ] **Step 4: Review the final diff against the design spec**

Verify every changed line maps to relations behavior, configuration, tests, docs, or release metadata. Confirm existing untracked files remain untouched and no credentials or app tokens appear in descriptors, errors, tests, or documentation.
