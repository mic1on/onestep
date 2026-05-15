from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "src"))

from onestep_control_plane_api.db.session import SessionLocal
from onestep_control_plane_api.ops.retention import run_retention


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Review or delete historical control-plane data based on retention settings. "
            "Defaults to dry-run for safety."
        )
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be deleted without mutating data.",
    )
    mode.add_argument(
        "--execute",
        action="store_true",
        help="Delete rows that are older than the configured retention thresholds.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=None,
        help="Override the configured delete batch size for this run.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    execute = bool(args.execute)
    with SessionLocal() as session:
        report = run_retention(
            session,
            execute=execute,
            batch_size=args.batch_size,
        )
    print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
