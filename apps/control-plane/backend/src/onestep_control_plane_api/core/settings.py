from __future__ import annotations

import json
import os
from json import JSONDecodeError
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "onestep-control-plane-api"
    app_env: str = "dev"
    debug: bool = False
    instance_offline_after_s: int = Field(default=90, ge=1)
    instance_health_participation_window_s: int = Field(default=3600, ge=1)
    database_url: str = (
        "postgresql+psycopg://postgres:postgres@localhost:5432/onestep_control_plane"
    )
    ingest_tokens: Annotated[list[str], NoDecode] = Field(default_factory=list)
    console_auth_username: str = ""
    console_auth_password: str = ""
    console_auth_session_ttl_s: int = Field(default=60 * 60 * 24 * 7, ge=60)
    cors_allow_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    api_response_timezone: str = ""
    notification_missed_start_scan_interval_s: int = Field(default=60, ge=5)
    notification_delivery_timeout_s: float = Field(default=5.0, gt=0)

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

    @property
    def effective_api_response_timezone(self) -> ZoneInfo:
        candidate = self.api_response_timezone.strip() or os.environ.get("TZ", "").strip() or "UTC"
        try:
            return ZoneInfo(candidate)
        except ZoneInfoNotFoundError:
            return ZoneInfo("UTC")


settings = Settings()
