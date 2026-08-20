#!/usr/bin/env python3
"""Fail when the mysql and postgres plugins' parallel implementations drift.

The two plugins deliberately carry line-identical (modulo dialect strings)
implementations of shared machinery: the SQLAlchemy state/cursor stores and
the table-sink update-policy helpers. Every change to one side must be ported
to the other; when it is not, users get divergence bugs (see issue #125: the
datetime cursor fix shipped for mysql three days before postgres).

This script diffs the normalized source of each paired element and exits
non-zero when anything drifts that is not on the explicit allowlist below.

Usage:
    python scripts/check_plugin_drift.py [--verbose]

Exit codes:
    0 - all pairs in sync
    1 - drift detected (printed per element with a unified diff)
"""

from __future__ import annotations

import argparse
import ast
import difflib
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# The parallel implementations now live in the canonical onestep-sql package
# (the legacy plugins/onestep-mysql and plugins/onestep-postgres distributions
# are thin forwarders since the Phase 1 consolidation, issue #133). Keep
# monitoring the real code until the Phase 2 _shared extraction retires the
# parallel copies entirely.
MYSQL_SRC = REPO_ROOT / "plugins" / "onestep-sql" / "src" / "onestep_sql" / "mysql"
POSTGRES_SRC = REPO_ROOT / "plugins" / "onestep-sql" / "src" / "onestep_sql" / "postgres"

# Token normalization applied to the mysql side so parallel code compares
# equal. Order matters (longest/most specific first).
NORMALIZATIONS: tuple[tuple[str, str], ...] = (
    ("onestep_mysql", "onestep_postgres"),
    ("MySQLConnector", "PostgresConnector"),
    ("MySQL", "Postgres"),
    ("MYSQL", "POSTGRES"),
    ("TableSink", "PostgresTableSink"),
    ("mysql", "postgres"),
    ("asyncmy", "psycopg"),
)

# Elements that are allowed to differ, with the reason each divergence is
# intentional. Keys are (pair_id, element_id). Add entries only with a reason;
# reviewers should challenge every new entry.
ALLOWLIST: dict[tuple[str, str], str] = {
    ("state-store", "_async_dsn"): (
        "driver mapping is inherently per-database "
        "(mysql+asyncmy vs postgresql+psycopg)"
    ),
}


@dataclass(frozen=True)
class Pair:
    pair_id: str
    mysql_file: str
    postgres_file: str
    # "*" compares the whole file; otherwise a list of element ids, where an
    # element is a dotted name like "_normalize_update_columns" or
    # "PostgresTableSink._update_payload". An id prefixed with "mysql:" on the
    # left of "=" maps a differing name, e.g. "TableSink._update_payload".
    elements: tuple[str, ...]


PAIRS: tuple[Pair, ...] = (
    Pair(
        pair_id="state-store",
        mysql_file="state_sqlalchemy.py",
        postgres_file="state_sqlalchemy.py",
        elements=("*",),
    ),
    Pair(
        pair_id="table-sink-policy",
        mysql_file="connector.py",
        postgres_file="connector.py",
        elements=(
            "_UPDATE_COLUMN_POLICIES",
            "_normalize_update_columns",
            "TableSink._update_payload",
            "TableSink._coerce_json_values",
        ),
    ),
    Pair(
        pair_id="incremental-state-key",
        mysql_file="connector.py",
        postgres_file="connector.py",
        elements=("_default_incremental_state_key",),
    ),
)


def normalize(text: str) -> str:
    for old, new in NORMALIZATIONS:
        text = text.replace(old, new)
    return text


def _class_methods(tree: ast.AST) -> dict[str, dict[str, ast.FunctionDef]]:
    methods: dict[str, dict[str, ast.FunctionDef]] = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            methods[node.name] = {
                sub.name: sub
                for sub in node.body
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
    return methods


def collect_elements(path: Path) -> dict[str, str]:
    """Return element_id -> source text for a module's comparable elements."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    elements: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            elements[node.name] = ast.get_source_segment(source, node) or ""
        elif isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            elements[node.targets[0].id] = ast.get_source_segment(source, node) or ""
    for class_name, method_map in _class_methods(tree).items():
        for method_name, method in method_map.items():
            elements[f"{class_name}.{method_name}"] = (
                ast.get_source_segment(source, method) or ""
            )
    return elements


def strip_allowlisted(source: str, pair_id: str) -> str:
    """Remove allowlisted functions from a full-file comparison."""
    tree = ast.parse(source)
    drop_lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if (pair_id, node.name) in ALLOWLIST:
                drop_lines.update(range(node.lineno, node.end_lineno + 1))
    kept = [
        line
        for number, line in enumerate(source.splitlines(), start=1)
        if number not in drop_lines
    ]
    return "\n".join(kept)


def check_pair(pair: Pair, verbose: bool) -> list[str]:
    mysql_path = MYSQL_SRC / pair.mysql_file
    postgres_path = POSTGRES_SRC / pair.postgres_file
    failures: list[str] = []

    if not mysql_path.exists() or not postgres_path.exists():
        missing = [
            str(p.relative_to(REPO_ROOT))
            for p in (mysql_path, postgres_path)
            if not p.exists()
        ]
        return [f"[{pair.pair_id}] missing file(s): {', '.join(missing)}"]

    mysql_elements = collect_elements(mysql_path)
    postgres_elements = collect_elements(postgres_path)

    def pg_key(element_id: str) -> str:
        # "TableSink._update_payload" -> "PostgresTableSink._update_payload"
        return element_id.replace("TableSink.", "PostgresTableSink.")

    for element_id in pair.elements:
        if element_id == "*":
            left = normalize(
                strip_allowlisted(mysql_path.read_text(encoding="utf-8"), pair.pair_id)
            )
            right = normalize(
                strip_allowlisted(postgres_path.read_text(encoding="utf-8"), pair.pair_id)
            )
            if left.splitlines() != right.splitlines():
                diff = "\n".join(
                    difflib.unified_diff(
                        left.splitlines(),
                        right.splitlines(),
                        fromfile=f"mysql/{pair.mysql_file} (normalized)",
                        tofile=f"postgres/{pair.postgres_file} (normalized)",
                        lineterm="",
                    )
                )
                failures.append(f"[{pair.pair_id}] whole-file drift:\n{diff}")
            continue

        left_src = mysql_elements.get(element_id)
        right_src = postgres_elements.get(pg_key(element_id))
        if left_src is None or right_src is None:
            missing_side = "mysql" if left_src is None else "postgres"
            failures.append(
                f"[{pair.pair_id}] element {element_id!r} not found on the "
                f"{missing_side} side"
            )
            continue
        left = normalize(left_src)
        right = normalize(right_src)
        if left.splitlines() != right.splitlines():
            diff = "\n".join(
                difflib.unified_diff(
                    left.splitlines(),
                    right.splitlines(),
                    fromfile=f"mysql {element_id} (normalized)",
                    tofile=f"postgres {pg_key(element_id)} (normalized)",
                    lineterm="",
                )
            )
            failures.append(f"[{pair.pair_id}] {element_id} drifted:\n{diff}")
        elif verbose:
            print(f"ok: [{pair.pair_id}] {element_id} in sync")

    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verbose", action="store_true", help="log in-sync pairs too")
    args = parser.parse_args(argv)

    failures: list[str] = []
    for pair in PAIRS:
        failures.extend(check_pair(pair, args.verbose))

    if not failures:
        print(f"plugin drift check: all {len(PAIRS)} pair group(s) in sync")
        return 0

    print("plugin drift check FAILED\n")
    print(
        "The mysql and postgres plugins keep these implementations parallel "
        "on purpose. Port your change to the other side, or - if the "
        "divergence is intentional - add an ALLOWLIST entry in "
        "scripts/check_plugin_drift.py with a reason.\n"
    )
    for failure in failures:
        print(failure)
        print()
    return 1


if __name__ == "__main__":
    sys.exit(main())
