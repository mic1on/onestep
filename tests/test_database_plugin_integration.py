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
