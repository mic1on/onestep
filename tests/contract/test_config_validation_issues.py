"""Contract guard: collected strict issues must agree with fail-fast validation.

``validate_app_config`` is fail-fast: it raises on the first problem, and many
tests (and the human CLI path) depend on that exact message. ``collect_app_config_issues``
walks the same units and reports every problem, which is what ``check --strict
--json`` uses so a caller can fix N mistakes in one run instead of N.

The two must not drift: if a validator is added to one path only, either the
human message changes or the JSON report silently misses a class of error. The
central assertion here is that the *first* collected issue is byte-identical to
the fail-fast exception for the same document, and that collect mode is never
empty when fail-fast raises.
"""
from __future__ import annotations

import pytest

from onestep.config import (
    AppConfigValidationError,
    collect_app_config_issues,
    load_app_config,
    validate_app_config,
)

VALID = {
    "apiVersion": "onestep/v1alpha1",
    "kind": "App",
    "app": {"name": "demo"},
    "resources": {"tick": {"type": "interval", "minutes": 5}},
    "tasks": [{"name": "x", "source": "tick", "handler": {"ref": "a.b:c"}}],
}


def _with(**overrides):
    document = {**VALID}
    document.update(overrides)
    return document


# Each case is a document that fails strict validation, plus the path we expect
# the first issue to be attributed to.
FAILING_CASES = {
    "unknown_top_level": _with(bogus=1),
    "unknown_task_field": _with(tasks=[{"name": "x", "source": "tick", "concurrencyy": 3}]),
    "unknown_resource_field": _with(resources={"tick": {"type": "interval", "minutes": 5, "nope": 1}}),
    "bad_api_version": _with(apiVersion="onestep/v9"),
    "bad_kind": _with(kind="NotApp"),
    "missing_api_version": {"kind": "App", "app": {"name": "demo"}},
    "unsupported_resource_type": _with(resources={"r": {"type": "no_such_type"}}),
    "bad_logging_format": _with(app={"name": "demo", "logging": {"level": "INFO", "format": "xml"}}),
    "task_without_handler_or_emit": _with(tasks=[{"name": "x", "source": "tick"}]),
    "bad_retry_type": _with(
        tasks=[{"name": "x", "source": "tick", "retry": {"type": "no_such_retry"}}]
    ),
    "app_not_mapping": _with(app="nope"),
    "tasks_not_list": _with(tasks="nope"),
    "legacy_mixed_with_app": _with(name="demo"),
    "dollar_schema_not_string": _with(**{"$schema": 5}),
}


@pytest.mark.parametrize("case", sorted(FAILING_CASES))
def test_collect_mode_agrees_with_fail_fast(case: str) -> None:
    document = FAILING_CASES[case]

    with pytest.raises(Exception) as caught:
        validate_app_config(document)
    fail_fast_message = str(caught.value)

    issues = collect_app_config_issues(document)
    assert issues, f"{case}: collect mode reported nothing while fail-fast raised"

    # The first issue must be the same problem the fail-fast path reports, so
    # the human message and the JSON report can never describe different errors.
    assert issues[0].message == fail_fast_message, case
    assert issues[0].path, case
    assert issues[0].kind, case


@pytest.mark.parametrize("case", sorted(FAILING_CASES))
def test_validation_error_carries_every_issue(case: str) -> None:
    """``AppConfigValidationError`` must expose structured, JSON-ready issues."""
    document = FAILING_CASES[case]
    with pytest.raises(AppConfigValidationError) as caught:
        load_app_config(document, strict=True, collect_all_issues=True)

    issues = caught.value.issues
    assert len(issues) == len(collect_app_config_issues(document))
    payload = caught.value.to_dict()
    assert payload["issues"][0] == issues[0].to_dict()
    for issue in payload["issues"]:
        assert set(issue) == {"path", "message", "kind"}


def test_collect_mode_reports_multiple_independent_problems() -> None:
    """A document with several mistakes yields all of them in one pass.

    This is the whole point of collect mode: one run instead of N.
    """
    document = {
        "apiVersion": "onestep/v1alpha1",
        "kind": "App",
        "app": {"name": "demo"},
        "bogus_top": 1,
        "resources": {
            "tick": {"type": "interval", "minutes": 5, "bogus_res": 1},
            "queue": {"type": "memory"},
        },
        "tasks": [{"name": "x", "source": "tick", "concurrencyy": 3}],
    }
    issues = collect_app_config_issues(document)
    assert len(issues) >= 4, [i.to_dict() for i in issues]

    paths = {issue.path for issue in issues}
    assert "config" in paths
    assert "resources.tick" in paths
    assert "tasks[0]" in paths


def test_collect_mode_returns_empty_for_valid_document() -> None:
    assert collect_app_config_issues(VALID) == ()
    assert collect_app_config_issues(_with(**{"$schema": "https://example.com/s.json"})) == ()


def test_default_load_path_stays_fail_fast() -> None:
    """Without the opt-in flag, strict loading must still raise the plain error.

    ``load_app_config`` is called by user code and other tests that assert the
    original exception type, so collection must stay opt-in.
    """
    with pytest.raises(ValueError) as caught:
        load_app_config(_with(bogus=1), strict=True)
    assert not isinstance(caught.value, AppConfigValidationError)
    assert str(caught.value) == "unsupported fields for config: bogus"


def test_issue_kinds_are_classified() -> None:
    document = {
        "apiVersion": "onestep/v1alpha1",
        "kind": "App",
        "app": {"name": "demo"},
        "bogus_top": 1,
        "resources": {"queue": {"type": "memory"}},
        "tasks": [{"name": "x", "source": "tick"}],
    }
    kinds = {issue.kind for issue in collect_app_config_issues(document)}
    assert "unknown_field" in kinds
