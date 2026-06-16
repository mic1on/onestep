from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from pathlib import Path

from onestep_worker_agent.client import register_agent, run_control_loop
from onestep_worker_agent.config import (
    StoredAgentConfig,
    config_path,
    default_config_dir,
    load_config,
    load_stored_config,
    save_stored_config,
)
from onestep_worker_agent.identity import load_identity, save_identity
from onestep_worker_agent.state import DeploymentStateStore
from onestep_worker_agent.supervisor import SubprocessSupervisor


async def start(config_dir: Path | str | None = None) -> None:
    config = load_config(config_dir)
    identity = load_identity(config.identity_path)
    if identity is None:
        identity = await register_agent(config)
        save_identity(config.identity_path, identity)

    supervisor = SubprocessSupervisor(
        work_dir=config.work_dir,
        max_concurrent_deployments=config.max_concurrent_deployments,
        state_store=DeploymentStateStore(config.deployment_state_path),
    )
    recovered = supervisor.recover_running_deployments()
    print(f"worker agent ready: {identity.worker_agent_id}")
    if recovered:
        print(f"recovered deployments: {', '.join(recovered)}")
    await run_control_loop(config=config, identity=identity, supervisor=supervisor)


def setup(args: argparse.Namespace) -> int:
    target_config_path = config_path(args.config_dir)
    existing = load_stored_config(args.config_dir)
    if existing is not None and not args.force:
        print(f"worker agent config exists: {target_config_path}")
        config = existing
    else:
        try:
            config = _collect_setup_config(args, existing)
        except ValueError as exc:
            print(f"onestep-worker-agent: setup failed: {exc}", file=sys.stderr)
            return 2
        written_path = save_stored_config(config, args.config_dir)
        print(f"worker agent config written: {written_path}")

    if args.no_start:
        _print_setup_summary(config, target_config_path)
        return 0

    return _run_start(args.config_dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="onestep-worker-agent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start_parser = subparsers.add_parser("start", help="Start the worker agent")
    start_parser.add_argument(
        "--config-dir",
        type=Path,
        default=None,
        help="Directory containing worker agent config.json",
    )

    setup_parser = subparsers.add_parser(
        "setup",
        help="Configure and optionally start the worker agent",
    )
    setup_parser.add_argument(
        "--config-dir",
        type=Path,
        default=None,
        help="Directory for worker agent config.json",
    )
    setup_parser.add_argument("--plane-url", default=None, help="Control Plane base URL")
    setup_parser.add_argument(
        "--registration-token",
        default=None,
        help="Worker-agent registration token",
    )
    setup_parser.add_argument("--name", default=None, help="Worker agent display name")
    setup_parser.add_argument(
        "--work-dir",
        type=Path,
        default=None,
        help="Worker agent state directory",
    )
    setup_parser.add_argument(
        "--max-concurrency",
        type=int,
        default=None,
        help="Maximum concurrent deployments",
    )
    setup_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing config.json",
    )
    setup_parser.add_argument(
        "--no-start",
        action="store_true",
        help="Write or validate config without starting the agent",
    )

    args = parser.parse_args(argv)
    if args.command == "start":
        return _run_start(args.config_dir)
    if args.command == "setup":
        return setup(args)
    return 2


def _run_start(config_dir: Path | str | None = None) -> int:
    try:
        asyncio.run(start(config_dir=config_dir))
    except Exception as exc:
        print(f"onestep-worker-agent: failed to start: {exc}", file=sys.stderr)
        return 1
    return 0


def _collect_setup_config(
    args: argparse.Namespace,
    existing: StoredAgentConfig | None,
) -> StoredAgentConfig:
    config_dir = Path(args.config_dir).expanduser() if args.config_dir else default_config_dir()
    plane_url = _resolve_string_value(
        cli_value=args.plane_url,
        existing_value=existing.plane_url if existing else None,
        prompt="Control Plane URL",
        default="http://localhost:8000",
    ).rstrip("/")
    registration_token = _resolve_string_value(
        cli_value=args.registration_token,
        existing_value=existing.registration_token if existing else None,
        prompt="Registration token",
        secret=True,
    )
    display_name = _resolve_string_value(
        cli_value=args.name,
        existing_value=existing.display_name if existing else None,
        prompt="Agent name",
        default="worker-agent",
    )
    work_dir = args.work_dir
    if work_dir is None:
        work_dir_value = _resolve_string_value(
            cli_value=None,
            existing_value=str(existing.work_dir) if existing else None,
            prompt="Worker agent directory",
            default=str(config_dir),
        )
        work_dir = Path(work_dir_value).expanduser()
    max_concurrent_deployments = args.max_concurrency
    if max_concurrent_deployments is None:
        raw_max_concurrency = _resolve_string_value(
            cli_value=None,
            existing_value=(
                str(existing.max_concurrent_deployments) if existing else None
            ),
            prompt="Max concurrency",
            default="1",
        )
        max_concurrent_deployments = _parse_positive_int(raw_max_concurrency, "max concurrency")
    elif max_concurrent_deployments < 1:
        raise ValueError("max concurrency must be at least 1")
    return StoredAgentConfig(
        plane_url=plane_url,
        registration_token=registration_token,
        work_dir=work_dir,
        display_name=display_name,
        max_concurrent_deployments=max_concurrent_deployments,
    )


def _resolve_string_value(
    *,
    cli_value: str | None,
    existing_value: str | None,
    prompt: str,
    default: str | None = None,
    secret: bool = False,
) -> str:
    if cli_value is not None and cli_value.strip():
        return cli_value.strip()
    if existing_value is not None and existing_value.strip():
        default = existing_value.strip()
    if not sys.stdin.isatty():
        if default is not None and default.strip():
            return default.strip()
        raise ValueError(f"{prompt} is required; pass it as a setup option or run interactively")
    suffix = f" [{default}]" if default else ""
    if secret:
        value = getpass.getpass(f"{prompt}{suffix}: ")
    else:
        value = input(f"{prompt}{suffix}: ")
    if value.strip():
        return value.strip()
    if default is not None and default.strip():
        return default.strip()
    raise ValueError(f"{prompt} is required")


def _parse_positive_int(value: str, label: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{label} must be a positive integer") from exc
    if parsed < 1:
        raise ValueError(f"{label} must be at least 1")
    return parsed


def _print_setup_summary(config: StoredAgentConfig, path: Path) -> None:
    print(f"Config: {path}")
    print(f"Plane URL: {config.plane_url}")
    print(f"Agent name: {config.display_name}")
    print(f"Worker dir: {config.work_dir}")
    print(f"Max concurrency: {config.max_concurrent_deployments}")
