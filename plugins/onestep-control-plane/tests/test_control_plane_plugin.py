from __future__ import annotations

from onestep import OneStepApp
from onestep.reporter_registry import ReporterRegistry, build_reporter, validate_reporter_spec
from onestep_control_plane import ControlPlaneReporter, register


def test_register_adds_control_plane_reporter_type(monkeypatch) -> None:
    monkeypatch.setenv("ONESTEP_CONTROL_URL", "https://control-plane.example.com")
    monkeypatch.setenv("ONESTEP_CONTROL_TOKEN", "secret-token")
    registry = ReporterRegistry()
    register(registry)
    spec = {"type": "control_plane"}

    validate_reporter_spec(spec, registry=registry)
    reporter = build_reporter(OneStepApp("plugin-app"), spec, registry=registry)

    assert isinstance(reporter, ControlPlaneReporter)
    assert reporter.config.base_url == "https://control-plane.example.com"
    assert reporter.config.token == "secret-token"
    assert reporter.config.service_name == "plugin-app"
