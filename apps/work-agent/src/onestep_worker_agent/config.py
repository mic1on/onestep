from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

CONFIG_FILENAME = "config.json"


@dataclass(frozen=True)
class AgentConfig:
    plane_url: str
    registration_token: str
    work_dir: Path
    identity_path: Path
    deployment_state_path: Path
    display_name: str
    max_concurrent_deployments: int


@dataclass(frozen=True)
class StoredAgentConfig:
    plane_url: str
    registration_token: str
    work_dir: Path
    display_name: str
    max_concurrent_deployments: int


def default_config_dir() -> Path:
    configured = os.environ.get("ONESTEP_WORKER_AGENT_CONFIG_DIR")
    if configured:
        return Path(configured).expanduser()
    configured = os.environ.get("ONESTEP_WORKER_AGENT_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".onestep" / "worker-agent"


def config_path(config_dir: Path | str | None = None) -> Path:
    root = Path(config_dir).expanduser() if config_dir is not None else default_config_dir()
    return root / CONFIG_FILENAME


def load_stored_config(config_dir: Path | str | None = None) -> StoredAgentConfig | None:
    path = config_path(config_dir)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"worker agent config is invalid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"worker agent config must be a JSON object: {path}")
    return _stored_config_from_mapping(payload, default_work_dir=path.parent)


def save_stored_config(
    config: StoredAgentConfig,
    config_dir: Path | str | None = None,
) -> Path:
    path = config_path(config_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "plane_url": config.plane_url,
        "registration_token": config.registration_token,
        "work_dir": str(config.work_dir),
        "display_name": config.display_name,
        "max_concurrent_deployments": config.max_concurrent_deployments,
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def load_config(config_dir: Path | str | None = None) -> AgentConfig:
    stored = load_stored_config(config_dir)
    root = config_path(config_dir).parent
    plane_url = _env_or_stored("ONESTEP_PLANE_URL", stored, "plane_url")
    if not plane_url:
        raise ValueError(
            "missing worker agent plane URL; run `onestep-worker-agent setup` "
            "or set ONESTEP_PLANE_URL"
        )
    registration_token = _env_or_stored(
        "ONESTEP_AGENT_REGISTRATION_TOKEN",
        stored,
        "registration_token",
        default="",
    )
    work_dir_value = _env_or_stored(
        "ONESTEP_WORKER_AGENT_DIR",
        stored,
        "work_dir",
        default=str(root),
    )
    display_name = _env_or_stored(
        "ONESTEP_WORKER_AGENT_NAME",
        stored,
        "display_name",
        default="worker-agent",
    )
    max_concurrent_deployments = int(
        _env_or_stored(
            "ONESTEP_WORKER_AGENT_MAX_CONCURRENCY",
            stored,
            "max_concurrent_deployments",
            default="1",
        )
    )
    work_dir = Path(work_dir_value).expanduser()
    return AgentConfig(
        plane_url=plane_url,
        registration_token=registration_token,
        work_dir=work_dir,
        identity_path=work_dir / "identity.json",
        deployment_state_path=work_dir / "deployments.json",
        display_name=display_name,
        max_concurrent_deployments=max_concurrent_deployments,
    )


def load_config_from_env() -> AgentConfig:
    return load_config()


def _env_or_stored(
    env_name: str,
    stored: StoredAgentConfig | None,
    field_name: str,
    *,
    default: str = "",
) -> str:
    value = os.environ.get(env_name)
    if value is not None:
        return value.strip()
    if stored is not None:
        stored_value = getattr(stored, field_name)
        return str(stored_value).strip()
    return default


def _stored_config_from_mapping(
    payload: dict[str, object],
    *,
    default_work_dir: Path,
) -> StoredAgentConfig:
    plane_url = _required_string(payload, "plane_url").rstrip("/")
    registration_token = _required_string(payload, "registration_token")
    display_name = _optional_string(payload, "display_name", "worker-agent")
    work_dir = Path(_optional_string(payload, "work_dir", str(default_work_dir))).expanduser()
    max_concurrent_deployments = _positive_int(
        payload.get("max_concurrent_deployments", 1),
        "max_concurrent_deployments",
    )
    return StoredAgentConfig(
        plane_url=plane_url,
        registration_token=registration_token,
        work_dir=work_dir,
        display_name=display_name,
        max_concurrent_deployments=max_concurrent_deployments,
    )


def _required_string(payload: dict[str, object], field_name: str) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"worker agent config field {field_name!r} is required")
    return value.strip()


def _optional_string(
    payload: dict[str, object],
    field_name: str,
    default: str,
) -> str:
    value = payload.get(field_name)
    if value is None:
        return default
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"worker agent config field {field_name!r} must be a string")
    return value.strip()


def _positive_int(value: object, field_name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"worker agent config field {field_name!r} must be a positive integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"worker agent config field {field_name!r} must be a positive integer"
        ) from exc
    if parsed < 1:
        raise ValueError(f"worker agent config field {field_name!r} must be at least 1")
    return parsed
