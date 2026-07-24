from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "src"))

from onestep_control_plane_api.auth.passwords import MIN_PASSWORD_LENGTH
from onestep_control_plane_api.auth.service import LocalAuthError, LocalAuthService
from onestep_control_plane_api.db.session import SessionLocal


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a local OneStep control plane admin user.")
    parser.add_argument("--username", required=True, help="Admin username")
    parser.add_argument(
        "--password",
        default=None,
        help=(
            "Admin password. If omitted, the script prompts securely. "
            f"Must be at least {MIN_PASSWORD_LENGTH} characters."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    password = args.password if args.password is not None else getpass.getpass("Password: ")
    with SessionLocal() as session:
        service = LocalAuthService(session)
        try:
            identity = service.create_user(
                username=args.username,
                password=password,
                role_names=["admin"],
            )
        except (LocalAuthError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    print(f"created local admin {identity.username} with roles={','.join(identity.roles)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
