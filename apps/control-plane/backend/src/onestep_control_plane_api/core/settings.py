from __future__ import annotations

import json
import os
from json import JSONDecodeError
from typing import Annotated
from urllib.parse import urljoin
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://postgres:postgres@localhost:5432/onestep_control_plane"
)


class Settings(BaseSettings):
    app_name: str = "onestep-control-plane-api"
    app_env: str = "dev"
    debug: bool = False
    instance_offline_after_s: int = Field(default=90, ge=1)
    instance_health_participation_window_s: int = Field(default=3600, ge=1)
    database_url: str = DEFAULT_DATABASE_URL
    ingest_tokens: Annotated[list[str], NoDecode] = Field(default_factory=list)
    console_auth_username: str = ""
    console_auth_password: str = ""
    console_auth_session_ttl_s: int = Field(default=60 * 60 * 24 * 7, ge=60)
    console_sensitive_auth_window_s: int = Field(default=15 * 60, ge=60)
    console_base_url: str = ""
    cors_allow_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    api_response_timezone: str = ""
    notification_missed_start_scan_interval_s: int = Field(default=60, ge=5)
    notification_delivery_timeout_s: float = Field(default=5.0, gt=0)
    background_worker_leader_poll_interval_s: int = Field(default=5, ge=1)
    readiness_task_stale_after_s: int = Field(default=120, ge=5)
    retention_task_events_days: int = Field(default=30, ge=1)
    retention_task_metric_windows_days: int = Field(default=90, ge=1)
    retention_agent_commands_days: int = Field(default=30, ge=1)
    retention_delete_batch_size: int = Field(default=1000, ge=1)
    retention_run_interval_s: int = Field(default=60 * 60 * 24, ge=60)
    pipeline_credentials_fernet_key: str = ""

    model_config = SettingsConfigDict(
        env_prefix="ONESTEP_CP_",
        env_file=".env",
        extra="ignore",
    )

    @field_validator("ingest_tokens", "cors_allow_origins", mode="before")
    @classmethod
    def parse_string_list(cls, value: object) -> object:
        if isinstance(value, str):
            candidate = value.strip()
            if not candidate:
                return []
            if candidate.startswith("["):
                try:
                    loaded = json.loads(candidate)
                except JSONDecodeError:
                    return [item.strip() for item in candidate.split(",") if item.strip()]
                if isinstance(loaded, list):
                    return [str(item).strip() for item in loaded if str(item).strip()]
            return [item.strip() for item in candidate.split(",") if item.strip()]
        return value

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_database_url(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return DEFAULT_DATABASE_URL
        return value

    @field_validator("pipeline_credentials_fernet_key")
    @classmethod
    def validate_pipeline_credentials_fernet_key(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            return ""
        from cryptography.fernet import Fernet

        Fernet(stripped.encode("ascii"))
        return stripped

    @model_validator(mode="after")
    def validate_console_auth_pair(self) -> Settings:
        has_username = bool(self.console_auth_username.strip())
        has_password = bool(self.console_auth_password.strip())
        if has_username != has_password:
            raise ValueError(
                "ONESTEP_CP_CONSOLE_AUTH_USERNAME and "
                "ONESTEP_CP_CONSOLE_AUTH_PASSWORD must be set together"
            )
        if self.instance_health_participation_window_s < self.instance_offline_after_s:
            raise ValueError(
                "ONESTEP_CP_INSTANCE_HEALTH_PARTICIPATION_WINDOW_S must be greater than or equal "
                "to ONESTEP_CP_INSTANCE_OFFLINE_AFTER_S"
            )
        return self

    @property
    def ingest_auth_configured(self) -> bool:
        return bool(self.ingest_tokens)

    @property
    def console_auth_configured(self) -> bool:
        return bool(self.console_auth_username.strip() and self.console_auth_password.strip())

    def build_console_url(self, path: str | None) -> str | None:
        if path is None:
            return None
        normalized_path = path.strip()
        if not normalized_path:
            return None
        if normalized_path.startswith(("http://", "https://")):
            return normalized_path

        base_url = self.console_base_url.strip()
        if not base_url:
            return normalized_path
        return urljoin(f"{base_url.rstrip('/')}/", normalized_path.lstrip("/"))

    @property
    def effective_api_response_timezone(self) -> ZoneInfo:
        candidate = self.api_response_timezone.strip() or os.environ.get("TZ", "").strip() or "UTC"
        try:
            return ZoneInfo(candidate)
        except ZoneInfoNotFoundError:
            return ZoneInfo("UTC")


settings = Settings()
