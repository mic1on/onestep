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
        plugin_text = (ROOT / "plugins" / package / "pyproject.toml").read_text(encoding="utf-8")
        assert "onestep = { workspace = true }" in plugin_text


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


def test_integration_harness_contains_database_services_and_tests() -> None:
    compose = (ROOT / "docker-compose.integration.yml").read_text(encoding="utf-8")
    setup = (ROOT / "scripts" / "setup-integration-env.sh").read_text(encoding="utf-8")
    runner = (ROOT / "scripts" / "run-integration-tests.sh").read_text(encoding="utf-8")
    assert "clickhouse:" in compose and "mongo:" in compose
    assert "ONESTEP_CLICKHOUSE_DSN" in setup and "ONESTEP_MONGODB_URI" in setup
    assert "plugins/onestep-clickhouse/tests/integration" in runner
    assert "plugins/onestep-mongodb/tests/integration" in runner
    assert "plugins/onestep-elasticsearch/tests/integration" not in runner


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


def test_public_docs_name_database_plugin_resources_and_semantics() -> None:
    files = [ROOT / "README.md", ROOT / "docs" / "yaml-task-definition.md", ROOT / "skills" / "onestep" / "references" / "connectors.md", ROOT / "CHANGELOG.md"]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in files)
    for value in ("onestep-elasticsearch", "elasticsearch_bulk_sink", "onestep-clickhouse", "clickhouse_table_sink", "onestep-mongodb", "mongodb_polling", "mongodb_change_stream", "mongodb_collection_sink"):
        assert value in combined
    assert "full_document: updateLookup" in combined
    assert "durable" in combined and "UNCERTAIN" in combined
