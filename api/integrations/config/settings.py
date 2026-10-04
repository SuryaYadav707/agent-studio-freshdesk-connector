"""Process-level settings from environment / .env (pydantic-settings)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from api.integrations.constants.constants import IntegrationsRouteNameMapping


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    connector_env: str = "local"  # selects environment_specific_fields block in integrations_config.json
    # NoDecode: keep the raw "a,b" string so the validator below can split it (no JSON parsing)
    connector_integrations: Annotated[list[str], NoDecode] = Field(default_factory=lambda: list(IntegrationsRouteNameMapping))
    credentials_dir: Path = Path("./tokens")
    ui_base_url: str = "http://localhost:8765"
    ui_secret: str | None = None  # signs the demo UI's session cookie; random per process when unset
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-opus-5-5"

    @field_validator("connector_integrations", mode="before")
    @classmethod
    def _split_csv(cls, value):
        if isinstance(value, str):
            value = [v.strip() for v in value.split(",") if v.strip()]
        unknown = [v for v in value if v not in IntegrationsRouteNameMapping]
        if unknown:
            raise ValueError(f"unknown integrations in CONNECTOR_INTEGRATIONS: {unknown}")
        return value

    @property
    def ui_port(self) -> int:
        from urllib.parse import urlparse

        parsed = urlparse(self.ui_base_url)
        return parsed.port or (443 if parsed.scheme == "https" else 80)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
