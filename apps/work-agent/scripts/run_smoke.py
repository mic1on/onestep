from __future__ import annotations

import argparse
import contextlib
import io
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
import zipfile
from pathlib import Path
from uuid import uuid4

import httpx

REGISTRATION_TOKEN = "smoke-worker-registration-token"
INGEST_TOKEN = "smoke-ingest-token"


class SmokeError(RuntimeError):
    pass


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)

    parser = argparse.ArgumentParser(
        description="Run a local end-to-end smoke test against a sibling control-plane repo."
    )
    parser.add_argument(
        "--control-plane-dir",
        type=Path,
        default=None,
        help="Path to the onestep-control-plane repository.",
    )
    parser.add_argument(
        "--timeout-s",
        type=float,
        default=90.0,
        help="Maximum seconds to wait for each async smoke phase.",
    )
    parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="Keep the temporary smoke directory for debugging.",
    )
    args = parser.parse_args()

    worker_agent_dir = Path(__file__).resolve().parents[1]
    control_plane_dir = (
        args.control_plane_dir.resolve()
        if args.control_plane_dir is not None
        else worker_agent_dir.parent / "onestep-control-plane"
    )
    _require_repo(worker_agent_dir, control_plane_dir)
    _require_executable("uv")

    temp_dir = Path(tempfile.mkdtemp(prefix="onestep-worker-agent-smoke-"))
    api_process: subprocess.Popen[str] | None = None
    agent_process: subprocess.Popen[str] | None = None
    logs: list[Path] = []
    try:
        api_url = f"http://127.0.0.1:{_free_port()}"
        print(f"smoke: temp dir {temp_dir}")
        print("smoke: migrating control-plane database")
        _run_migrations(control_plane_dir, temp_dir)

        api_process = _start_api(control_plane_dir, temp_dir, api_url, logs)
        _wait_for_json(
            f"{api_url}/healthz",
            lambda payload: payload.get("status") == "ok",
            label="control-plane health",
            process=api_process,
            timeout_s=args.timeout_s,
            logs=logs,
        )
        print(f"smoke: control-plane ready at {api_url}")

        agent_process = _start_agent(worker_agent_dir, temp_dir, api_url, logs)
        worker_agent = _wait_for_worker_agent_online(
            api_url,
            process=agent_process,
            timeout_s=args.timeout_s,
            logs=logs,
        )
        worker_agent_id = worker_agent["worker_agent_id"]
        print(f"smoke: worker agent online {worker_agent_id}")

        package = _upload_workflow_package(api_url)
        deployment = _create_deployment(
            api_url,
            worker_agent_id=worker_agent_id,
            workflow_package_id=package["package_id"],
        )
        deployment_id = deployment["deployment_id"]
        print(f"smoke: deployment created {deployment_id}")

        running_events = _wait_for_deployment_event(
            api_url,
            deployment_id=deployment_id,
            event_type="running",
            process=agent_process,
            timeout_s=args.timeout_s,
            logs=logs,
        )
        print(f"smoke: deployment running after {len(running_events)} events")

        _stop_deployment(api_url, deployment_id=deployment_id)
        stopped_events = _wait_for_deployment_event(
            api_url,
            deployment_id=deployment_id,
            event_type="stopped",
            process=agent_process,
            timeout_s=args.timeout_s,
            logs=logs,
        )
        event_types = ", ".join(item["event_type"] for item in stopped_events)
        print(f"smoke: deployment stopped; events: {event_types}")
        print("smoke ok")
        return 0
    except Exception as exc:
        print(f"smoke failed: {exc}", file=sys.stderr)
        _print_logs(logs)
        return 1
    finally:
        _terminate_process(agent_process)
        _terminate_process(api_process)
        if args.keep_temp:
            print(f"smoke: kept temp dir {temp_dir}")
        else:
            shutil.rmtree(temp_dir, ignore_errors=True)


def _require_repo(worker_agent_dir: Path, control_plane_dir: Path) -> None:
    if not (worker_agent_dir / "pyproject.toml").is_file():
        raise SmokeError(f"worker-agent repo not found at {worker_agent_dir}")
    if not (control_plane_dir / "alembic.ini").is_file():
        raise SmokeError(f"control-plane repo not found at {control_plane_dir}")


def _require_executable(name: str) -> None:
    if shutil.which(name) is None:
        raise SmokeError(f"{name!r} is required on PATH")


def _free_port() -> int:
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _control_plane_env(temp_dir: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("VIRTUAL_ENV", None)
    env.update(
        {
            "ONESTEP_CP_APP_ENV": "dev",
            "ONESTEP_CP_DATABASE_URL": f"sqlite+pysqlite:///{temp_dir / 'control-plane.db'}",
            "ONESTEP_CP_INGEST_TOKENS": INGEST_TOKEN,
            "ONESTEP_CP_WORKER_AGENT_REGISTRATION_TOKENS": REGISTRATION_TOKEN,
            "ONESTEP_CP_WORKER_PACKAGE_STORAGE_DIR": str(temp_dir / "packages"),
            "ONESTEP_CP_CONSOLE_AUTH_USERNAME": "",
            "ONESTEP_CP_CONSOLE_AUTH_PASSWORD": "",
        }
    )
    return env


def _run_migrations(control_plane_dir: Path, temp_dir: Path) -> None:
    completed = subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        cwd=control_plane_dir,
        env=_control_plane_env(temp_dir),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if completed.returncode != 0:
        raise SmokeError(
            "control-plane migrations failed\n"
            + completed.stdout[-4000:]
        )


def _start_api(
    control_plane_dir: Path,
    temp_dir: Path,
    api_url: str,
    logs: list[Path],
) -> subprocess.Popen[str]:
    port = api_url.rsplit(":", maxsplit=1)[1]
    stdout_path = temp_dir / "control-plane.stdout.log"
    stderr_path = temp_dir / "control-plane.stderr.log"
    logs.extend([stdout_path, stderr_path])
    return subprocess.Popen(
        [
            "uv",
            "run",
            "uvicorn",
            "onestep_control_plane_api.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            port,
            "--log-level",
            "warning",
        ],
        cwd=control_plane_dir,
        env=_control_plane_env(temp_dir),
        stdout=stdout_path.open("w"),
        stderr=stderr_path.open("w"),
        text=True,
        start_new_session=True,
    )


def _start_agent(
    worker_agent_dir: Path,
    temp_dir: Path,
    api_url: str,
    logs: list[Path],
) -> subprocess.Popen[str]:
    stdout_path = temp_dir / "worker-agent.stdout.log"
    stderr_path = temp_dir / "worker-agent.stderr.log"
    logs.extend([stdout_path, stderr_path])
    env = os.environ.copy()
    env.pop("VIRTUAL_ENV", None)
    env.update(
        {
            "ONESTEP_PLANE_URL": api_url,
            "ONESTEP_AGENT_REGISTRATION_TOKEN": REGISTRATION_TOKEN,
            "ONESTEP_WORKER_AGENT_DIR": str(temp_dir / "agent"),
            "ONESTEP_WORKER_AGENT_MAX_CONCURRENCY": "1",
            "ONESTEP_WORKER_AGENT_NAME": "smoke-agent",
            "PYTHONUNBUFFERED": "1",
        }
    )
    return subprocess.Popen(
        ["uv", "run", "onestep-worker-agent", "start"],
        cwd=worker_agent_dir,
        env=env,
        stdout=stdout_path.open("w"),
        stderr=stderr_path.open("w"),
        text=True,
        start_new_session=True,
    )


def _wait_for_json(
    url: str,
    predicate,
    *,
    label: str,
    process: subprocess.Popen[str],
    timeout_s: float,
    logs: list[Path],
) -> dict[str, object]:
    deadline = time.monotonic() + timeout_s
    last_error = ""
    with httpx.Client(timeout=5.0) as client:
        while time.monotonic() < deadline:
            _raise_if_exited(process, label, logs)
            try:
                response = client.get(url)
                payload = response.json()
                if response.status_code < 500 and predicate(payload):
                    return payload
                last_error = f"status={response.status_code} payload={payload}"
            except Exception as exc:
                last_error = str(exc)
            time.sleep(0.5)
    raise SmokeError(f"timed out waiting for {label}: {last_error}")


def _wait_for_worker_agent_online(
    api_url: str,
    *,
    process: subprocess.Popen[str],
    timeout_s: float,
    logs: list[Path],
) -> dict[str, object]:
    def _online(payload: dict[str, object]) -> bool:
        items = payload.get("items")
        return isinstance(items, list) and any(
            isinstance(item, dict)
            and item.get("display_name") == "smoke-agent"
            and item.get("status") == "online"
            for item in items
        )

    payload = _wait_for_json(
        f"{api_url}/api/v1/worker-agents",
        _online,
        label="worker agent online",
        process=process,
        timeout_s=timeout_s,
        logs=logs,
    )
    items = payload["items"]
    if not isinstance(items, list):
        raise SmokeError("worker agent response did not include items")
    for item in items:
        if (
            isinstance(item, dict)
            and item.get("display_name") == "smoke-agent"
            and item.get("status") == "online"
        ):
            return item
    raise SmokeError("worker agent came online but was not found in response")


def _upload_workflow_package(api_url: str) -> dict[str, object]:
    package_bytes = _build_workflow_package()
    with httpx.Client(base_url=api_url, timeout=30.0) as client:
        response = client.post(
            "/api/v1/workflow-packages",
            params={
                "workflow_id": str(uuid4()),
                "version": "smoke",
                "filename": "worker-agent-smoke.zip",
                "entrypoint": "worker.yaml",
            },
            headers={"content-type": "application/zip"},
            content=package_bytes,
        )
        response.raise_for_status()
        return response.json()


def _build_workflow_package() -> bytes:
    worker_yaml = textwrap.dedent(
        """
        apiVersion: onestep/v1alpha1
        kind: App

        app:
          name: worker-agent-smoke

        resources:
          tick:
            type: interval
            seconds: 3600
            immediate: false

        tasks:
          - name: noop
            source: tick
            handler:
              ref: smoke_tasks:handle
        """
    ).lstrip()
    task_module = textwrap.dedent(
        """
        async def handle(ctx, item):
            return None
        """
    ).lstrip()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("worker.yaml", worker_yaml)
        archive.writestr("smoke_tasks.py", task_module)
    return buffer.getvalue()


def _create_deployment(
    api_url: str,
    *,
    worker_agent_id: str,
    workflow_package_id: str,
) -> dict[str, object]:
    with httpx.Client(base_url=api_url, timeout=30.0) as client:
        response = client.post(
            "/api/v1/worker-deployments",
            json={
                "workflow_package_id": workflow_package_id,
                "worker_agent_id": worker_agent_id,
                "desired_status": "running",
            },
        )
        response.raise_for_status()
        return response.json()


def _stop_deployment(api_url: str, *, deployment_id: str) -> None:
    with httpx.Client(base_url=api_url, timeout=30.0) as client:
        response = client.post(f"/api/v1/worker-deployments/{deployment_id}/stop")
        response.raise_for_status()


def _wait_for_deployment_event(
    api_url: str,
    *,
    deployment_id: str,
    event_type: str,
    process: subprocess.Popen[str],
    timeout_s: float,
    logs: list[Path],
) -> list[dict[str, object]]:
    def _has_event(payload: dict[str, object]) -> bool:
        items = payload.get("items")
        return isinstance(items, list) and any(
            isinstance(item, dict) and item.get("event_type") == event_type
            for item in items
        )

    payload = _wait_for_json(
        f"{api_url}/api/v1/worker-deployments/{deployment_id}/events",
        _has_event,
        label=f"deployment event {event_type}",
        process=process,
        timeout_s=timeout_s,
        logs=logs,
    )
    items = payload["items"]
    if not isinstance(items, list):
        raise SmokeError("deployment event response did not include items")
    return [item for item in items if isinstance(item, dict)]


def _raise_if_exited(
    process: subprocess.Popen[str],
    label: str,
    logs: list[Path],
) -> None:
    returncode = process.poll()
    if returncode is not None:
        raise SmokeError(f"{label} process exited early with code {returncode}")


def _terminate_process(process: subprocess.Popen[str] | None) -> None:
    if process is None or process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except Exception:
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except Exception:
            process.kill()
        process.wait(timeout=10)


def _print_logs(logs: list[Path]) -> None:
    for path in logs:
        if not path.exists():
            continue
        content = path.read_text(errors="replace")
        if not content.strip():
            continue
        print(f"\n--- {path.name} ---", file=sys.stderr)
        print(content[-4000:], file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
