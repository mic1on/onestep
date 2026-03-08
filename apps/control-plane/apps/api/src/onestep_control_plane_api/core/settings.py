from __future__ import annotations

import json
from json import JSONDecodeError
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "onestep-control-plane-api"
    app_env: str = "dev"
    debug: bool = False
    instance_offline_after_s: int = 90
    database_url: str = (
        "postgresql+psycopg://postgres:postgres@localhost:5432/onestep_control_plane"
    )
    ingest_tokens: Annotated[list[str], NoDecode] = Field(default_factory=list)

    model_config = SettingsConfigDict(
        env_prefix="ONESTEP_CP_",
        env_file=".env",
        extra="ignore",
    )

    @field_validator("ingest_tokens", mode="before")
    @classmethod
    def parse_ingest_tokens(cls, value: object) -> object:
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

    @property
    def ingest_auth_configured(self) -> bool:
        return bool(self.ingest_tokens)


settings = Settings()
