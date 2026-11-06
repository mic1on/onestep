"""Contract tests for the shared table_queue claim/nack lint (issue #180).

``onestep_sql._shared.table_queue_lint.warn_empty_nack_with_claim`` is wired
into all three backend YAML builders (mysql / postgres / sqlite), so every
load path that resolves YAML resource specs surfaces the warning:
``onestep check`` (default and ``--strict``), the ``onestep build`` pre-build
check and ``onestep run`` startup. The lint never fails the load — the
configuration is valid, only risky — and never inspects ``ack`` because an
empty ``ack`` has a deliberate design (updating the business columns is the
completion marker).

Rationale (verified against all three ``connector.py`` modules): ``claim`` is
applied inside the fetch transaction, moving rows out of the ``where``
candidate set, while ``fail_row`` / ``retry_row`` / ``release_row`` all route
through ``update_row(row_ref, nack)``, which early-returns for an empty
mapping. With ``claim`` set and ``nack`` empty, failed rows therefore stick
in the claimed state — neither retried nor marked failed.
"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager

import pytest

from onestep.cli import main
from onestep.config import load_yaml_app

EXPECTED_SNIPPET = "nack is empty while claim is set"
EXPECTED_MESSAGE = (
    "resources.bidding_source: nack is empty while claim is set: "
    "failed rows will neither be retried nor marked failed; "
    "set nack fields (usually reverting the claim columns) if retry is intended."
)

# (connector resource type, table_queue resource type, dsn)
BACKENDS = {
    "mysql": ("mysql", "mysql_table_queue", "mysql+asyncmy://user:pass@127.0.0.1:3306/test"),
    "postgres": (
        "postgres",
        "postgres_table_queue",
        "postgresql+psycopg://user:pass@127.0.0.1:5432/test",
    ),
    "sqlite": ("sqlite", "sqlite_table_queue", "sqlite+aiosqlite:///:memory:"),
}

# The exact resource configuration from the issue's real-world case
# (onestep-tasks bidding_value_sync): claim moves rows out of the ``where``
# candidate set while ``nack: {}`` no-ops every failure path.
ISSUE_YAML = """\
name: bidding-sync
resources:
  mysql_conn:
    type: mysql
    dsn: mysql+asyncmy://user:pass@127.0.0.1:3306/test
sources:
  bidding_source:
    type: mysql_table_queue
    connector: mysql_conn
    table: bidding_values
    key: id
    where: score IS NULL AND state = 0 AND publish_date >= DATE_SUB(NOW(), INTERVAL 90 DAY)
    claim:
      score: 0
    ack: {}
    nack: {}
tasks:
  - name: sync
    source: bidding_source
    handler: builtins:print
"""


_CLAIM_SCORE = {"score": 0}
_EMPTY_MAPPING: dict = {}


def _render_scalar(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value)
    return repr(value)


def _field_block(name: str, mapping: dict | None, indent: int = 4) -> str:
    """Render a mapping field as a nested YAML block (``{name}: {}`` for empty)."""
    if mapping is None:
        return ""
    pad = " " * indent
    if not mapping:
        return f"{pad}{name}: {{}}\n"
    lines = [f"{pad}{name}:\n"]
    lines += [f"{pad}  {key}: {_render_scalar(value)}\n" for key, value in mapping.items()]
    return "".join(lines)


def _table_queue_yaml(
    backend: str,
    *,
    claim: dict | None = _CLAIM_SCORE,
    nack: dict | None = _EMPTY_MAPPING,
    ack: dict | None = _EMPTY_MAPPING,
    resource_name: str = "bidding_source",
    duplicate_section: bool = False,
) -> str:
    connector_type, queue_type, dsn = BACKENDS[backend]
    spec = "".join(
        [
            f"  {resource_name}:\n",
            f"    type: {queue_type}\n",
            "    connector: db\n",
            "    table: bidding_values\n",
            "    key: id\n",
            "    where: score IS NULL AND state = 0\n",
            _field_block("claim", claim),
            _field_block("ack", ack),
            _field_block("nack", nack),
        ]
    )
    # An identical spec repeated under ``resources:`` must not warn twice:
    # ``_collect_resource_specs`` deduplicates equal duplicates.
    resources_queue_section = spec if duplicate_section else ""
    return (
        "name: lint-worker\n"
        "resources:\n"
        "  db:\n"
        f"    type: {connector_type}\n"
        f"    dsn: {_render_scalar(dsn)}\n"
        f"{resources_queue_section}"
        "sources:\n"
        f"{spec}"
        "tasks:\n"
        "  - name: sync\n"
        f"    source: {resource_name}\n"
        "    handler: builtins:print\n"
    )


@contextmanager
def _isolated_root_logging():
    """Clear root handlers so WARNING records reach ``logging.lastResort``.

    pytest attaches capture handlers to the root logger, which would
    otherwise intercept the record before the last-resort stderr emission
    that ``onestep check`` relies on (it configures no handlers itself).
    Mirrors ``isolated_logging`` in ``tests/test_cli.py``.
    """
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    try:
        root.handlers = []
        root.setLevel(logging.WARNING)
        yield
    finally:
        root.handlers = saved_handlers
        root.setLevel(saved_level)


def _lint_warnings(caplog) -> list[logging.LogRecord]:
    return [
        record
        for record in caplog.records
        if record.name == "onestep"
        and record.levelno == logging.WARNING
        and EXPECTED_SNIPPET in record.getMessage()
    ]


def _write_worker(tmp_path, body: str) -> str:
    path = tmp_path / "worker.yaml"
    path.write_text(body, encoding="utf-8")
    return str(path)


# ---------------------------------------------------------------------------
# Load-level behaviour (both the default and the strict contract share the
# same resource-build pass, so one parametrization covers both).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("strict", [False, True], ids=["default", "strict"])
def test_issue_yaml_load_warns_for_mysql(tmp_path, caplog, strict: bool) -> None:
    path = _write_worker(tmp_path, ISSUE_YAML)

    with caplog.at_level(logging.WARNING, logger="onestep"):
        app = load_yaml_app(path, strict=strict)

    assert app.name == "bidding-sync"
    warnings = _lint_warnings(caplog)
    assert len(warnings) == 1
    assert warnings[0].getMessage() == EXPECTED_MESSAGE


@pytest.mark.parametrize("backend", sorted(BACKENDS))
@pytest.mark.parametrize("strict", [False, True], ids=["default", "strict"])
def test_all_three_backends_warn_identically(tmp_path, caplog, backend: str, strict: bool) -> None:
    path = _write_worker(tmp_path, _table_queue_yaml(backend))

    with caplog.at_level(logging.WARNING, logger="onestep"):
        load_yaml_app(path, strict=strict)

    warnings = _lint_warnings(caplog)
    assert len(warnings) == 1
    assert warnings[0].getMessage() == EXPECTED_MESSAGE


@pytest.mark.parametrize("backend", sorted(BACKENDS))
def test_missing_nack_warns_like_empty_nack(tmp_path, caplog, backend: str) -> None:
    # The connector factory coerces a missing nack with ``dict(nack or {})``,
    # so omitting the key is the same footgun as spelling ``nack: {}``.
    path = _write_worker(tmp_path, _table_queue_yaml(backend, nack=None))

    with caplog.at_level(logging.WARNING, logger="onestep"):
        load_yaml_app(path)

    assert len(_lint_warnings(caplog)) == 1


@pytest.mark.parametrize("backend", sorted(BACKENDS))
def test_no_warning_when_claim_and_nack_are_set(tmp_path, caplog, backend: str) -> None:
    path = _write_worker(
        tmp_path,
        _table_queue_yaml(backend, claim={"score": 0}, nack={"score": None, "state": 2}),
    )

    with caplog.at_level(logging.WARNING, logger="onestep"):
        load_yaml_app(path)

    assert _lint_warnings(caplog) == []


@pytest.mark.parametrize("backend", sorted(BACKENDS))
@pytest.mark.parametrize("nack", [None, {}], ids=["missing", "empty"])
def test_no_warning_when_claim_is_empty(tmp_path, caplog, backend: str, nack) -> None:
    # ``claim: {}`` never moves rows out of the candidate set, so an empty
    # nack has no silent-drop consequence and must stay silent.
    path = _write_worker(tmp_path, _table_queue_yaml(backend, claim={}, nack=nack))

    with caplog.at_level(logging.WARNING, logger="onestep"):
        load_yaml_app(path)

    assert _lint_warnings(caplog) == []


@pytest.mark.parametrize("backend", sorted(BACKENDS))
def test_empty_ack_alone_never_warns(tmp_path, caplog, backend: str) -> None:
    # ack:{} with claim and nack both set is a deliberate design (business
    # column updates are the completion marker) and must not trigger the lint.
    path = _write_worker(
        tmp_path,
        _table_queue_yaml(backend, claim={"score": 0}, nack={"state": 2}, ack={}),
    )

    with caplog.at_level(logging.WARNING, logger="onestep"):
        load_yaml_app(path)

    assert _lint_warnings(caplog) == []


def test_duplicate_resource_sections_warn_once(tmp_path, caplog) -> None:
    path = _write_worker(tmp_path, _table_queue_yaml("mysql", duplicate_section=True))

    with caplog.at_level(logging.WARNING, logger="onestep"):
        load_yaml_app(path)

    warnings = _lint_warnings(caplog)
    assert len(warnings) == 1
    assert warnings[0].getMessage() == EXPECTED_MESSAGE


def test_non_table_queue_resources_never_lint(tmp_path, caplog) -> None:
    path = _write_worker(
        tmp_path,
        """\
name: plain-worker
sources:
  jobs:
    type: memory
    maxsize: 10
tasks:
  - name: t
    source: jobs
    handler: builtins:print
""",
    )

    with caplog.at_level(logging.WARNING, logger="onestep"):
        app = load_yaml_app(path)

    assert app.name == "plain-worker"
    assert _lint_warnings(caplog) == []


# ---------------------------------------------------------------------------
# CLI surface: `onestep check` configures no logging handlers, so the
# warning reaches stderr through logging's last-resort handler and the exit
# code stays 0.
# ---------------------------------------------------------------------------


def test_cli_check_prints_warning_to_stderr(tmp_path, capsys) -> None:
    path = _write_worker(tmp_path, ISSUE_YAML)

    with _isolated_root_logging():
        exit_code = main(["check", path])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "App: bidding-sync" in captured.out
    assert EXPECTED_MESSAGE in captured.err
    assert EXPECTED_SNIPPET not in captured.out


def test_cli_check_strict_prints_warning_to_stderr(tmp_path, capsys) -> None:
    path = _write_worker(tmp_path, ISSUE_YAML)

    with _isolated_root_logging():
        exit_code = main(["check", "--strict", path])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "App: bidding-sync" in captured.out
    assert EXPECTED_MESSAGE in captured.err


def test_cli_check_json_stdout_stays_clean(tmp_path, capsys) -> None:
    path = _write_worker(tmp_path, ISSUE_YAML)

    with _isolated_root_logging():
        exit_code = main(["check", "--json", path])

    captured = capsys.readouterr()
    assert exit_code == 0
    summary = json.loads(captured.out)  # stdout must remain pure JSON
    assert summary["name"] == "bidding-sync"
    assert EXPECTED_SNIPPET not in captured.out
    assert EXPECTED_MESSAGE in captured.err
