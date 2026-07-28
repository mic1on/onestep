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
