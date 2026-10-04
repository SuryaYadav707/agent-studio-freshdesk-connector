"""Typed loader for integrations_config.json. `environment_specific_fields` values are
`${ENV_VAR}` / `${ENV_VAR:default}` placeholders resolved at load time (CONNECTOR_ENV picks the block).
"""
from __future__ import annotations

import json
import logging
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pydantic import BaseModel, Field

from api.integrations.constants.constants import IntegrationsDisplayToRoute, IntegrationsRouteNameMapping
from api.integrations.models.data_models import RateLimitsInfo
from api.integrations.models.errors import ConfigError

# Every entry point (CLI, UI, MCP server over stdio, scripts) reads secrets from .env.
load_dotenv()

log = logging.getLogger(__name__)
CONFIG_PATH = Path(__file__).with_name("integrations_config.json")
_PLACEHOLDER = re.compile(r"\$\{([A-Z0-9_]+)(?::([^}]*))?\}")


def _interpolate(value: str) -> str:
    """Replace ${VAR} / ${VAR:default} with environment values. Unset without default -> ''."""

    def repl(match: re.Match) -> str:
        var, default = match.group(1), match.group(2)
        return os.environ.get(var, default if default is not None else "")

    return _PLACEHOLDER.sub(repl, value)


class VersionedIntegrationInfo(BaseModel):
    """One version of one integration. OAuth-only fields stay empty for form auth and vice versa."""

    auth_type: str  # "oauth2" | "form"
    dynamic_loading: bool = False
    sync_type: str = "none"
    token_rotation: bool = False
    pkce: bool = False
    environment_specific_fields: dict[str, dict[str, str]] = Field(default_factory=dict)
    documentation_url: str | None = None
    api_base: str
    health_endpoint: str = "/"
    rate_limits: RateLimitsInfo = Field(default_factory=RateLimitsInfo)
    form_fields: list[str] = Field(default_factory=list)
    redirect_endpoint: str | None = None
    scope: str = ""
    scope_separator: str = " "
    authorization_url: str | None = None
    authorization_url_fields: list[str] = Field(default_factory=list)
    token_endpoint: str | None = None
    token_endpoint_fields: list[str] = Field(default_factory=list)
    refresh_token_endpoint_fields: list[str] = Field(default_factory=list)
    revoke_endpoint: str | None = None
    token_endpoint_headers: dict[str, str] = Field(default_factory=dict)
    token_type: str = "Bearer"
    action_scope_map: dict[str, list[str]] = Field(default_factory=dict)
    sensitive_scopes: dict[str, dict[str, Any]] = Field(default_factory=dict)

    def env_fields(self, env: str | None = None) -> dict[str, str]:
        """Resolved secrets/settings for the active environment."""
        env = env or os.environ.get("CONNECTOR_ENV", "local")
        fields = self.environment_specific_fields.get(env)
        if fields is None:
            raise ConfigError(f"no environment_specific_fields for CONNECTOR_ENV={env}")
        return {k: _interpolate(v) for k, v in fields.items()}

    def env_field(self, name: str, env: str | None = None) -> str | None:
        return self.env_fields(env).get(name) or None

    def require_env_field(self, name: str, env: str | None = None) -> str:
        value = self.env_field(name, env)
        if not value:
            raise ConfigError(f"missing value for '{name}'; set it in .env (see .env.example)")
        return value

    def default_scopes(self) -> list[str]:
        return [s for s in self.scope.split(self.scope_separator) if s]

    def scopes_for_actions(self, actions: list[str]) -> list[str]:
        """Default scopes plus every scope the given actions need, order kept, no duplicates."""
        out = self.default_scopes()
        for action in actions:
            for scope in self.action_scope_map.get(action, []):
                if scope not in out:
                    out.append(scope)
        return out


class VersionedIntegrationConfig(BaseModel):
    current_version: str
    versions: dict[str, VersionedIntegrationInfo]

    def resolve(self, version: str | None = None) -> tuple[str, VersionedIntegrationInfo]:
        """Unknown versions fall back to current_version (stored connections survive config bumps)."""
        if version is not None and version not in self.versions:
            log.warning("integration version %r not in config, falling back to %r", version, self.current_version)
        selected = version if version in self.versions else self.current_version
        return selected, self.versions[selected]


class IntegrationConfig(BaseModel):
    """Whole file, keyed by route name."""

    integrations: dict[str, VersionedIntegrationConfig]

    def get(self, name: str) -> VersionedIntegrationConfig:
        """Accept route name ("zoho_inventory") or display name ("Zoho Inventory")."""
        key = IntegrationsDisplayToRoute.get(name, name)
        if key not in self.integrations:
            raise ConfigError(f"integration '{name}' not configured")
        return self.integrations[key]


@lru_cache(maxsize=4)
def load_integration_config(path: Path = CONFIG_PATH) -> IntegrationConfig:
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    unknown = set(raw) - set(IntegrationsRouteNameMapping)
    if unknown:
        raise ConfigError(f"integrations_config.json has entries without constants: {sorted(unknown)}")
    return IntegrationConfig(integrations={k: VersionedIntegrationConfig(**v) for k, v in raw.items()})


def get_current_integration_version(name: str, version: str | None = None) -> tuple[str, VersionedIntegrationInfo]:
    return load_integration_config().get(name).resolve(version)


def get_integration_info(name: str, version: str | None = None) -> VersionedIntegrationInfo:
    return get_current_integration_version(name, version)[1]
