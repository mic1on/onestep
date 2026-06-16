from __future__ import annotations

import argparse
import asyncio

from onestep_worker_agent.client import register_agent, run_control_loop
from onestep_worker_agent.config import load_config_from_env
from onestep_worker_agent.identity import load_identity, save_identity
from onestep_worker_agent.supervisor import SubprocessSupervisor


async def start() -> None:
    config = load_config_from_env()
    identity = load_identity(config.identity_path)
    if identity is None:
        identity = await register_agent(config)
        save_identity(config.identity_path, identity)

    supervisor = SubprocessSupervisor(
        work_dir=config.work_dir,
        max_concurrent_deployments=config.max_concurrent_deployments,
    )
    print(f"worker agent ready: {identity.worker_agent_id}")
    await run_control_loop(config=config, identity=identity, supervisor=supervisor)


def main() -> None:
    parser = argparse.ArgumentParser(prog="onestep-worker-agent")
    parser.add_argument("command", choices=["start"])
    args = parser.parse_args()
    if args.command == "start":
        asyncio.run(start())
