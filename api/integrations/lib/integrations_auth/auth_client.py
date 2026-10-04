"""Auth routing (VectorShift auth_client.go).

get_auth_client() decides, by type, whether an integration gets the form-based
client or the OAuth client. Integrations never construct their own auth.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

from api.integrations.config.integrations_config import (
    VersionedIntegrationInfo,
    get_current_integration_version,
)
from api.integrations.config.settings import get_settings
from api.integrations.constants.constants import IntegrationsRouteNameMapping, IntegrationsWithFormAuthOnly
from api.integrations.lib.integrations_auth.credentials_store import CredentialsStore, FileCredentialsStore
from api.integrations.lib.integrations_auth.form_auth_client import CommonFormAuthClient
from api.integrations.lib.integrations_auth.oauth_client import OAuthClient
from api.integrations.models.errors import ConfigError


def get_auth_client_config(integration_type: str, version: str | None = None) -> tuple[str, VersionedIntegrationInfo]:
    return get_current_integration_version(integration_type, version)


@lru_cache(maxsize=1)
def default_store() -> CredentialsStore:
    return FileCredentialsStore(get_settings().credentials_dir)


def get_auth_client(
    integration_type: str,
    version: str | None = None,
    *,
    store: CredentialsStore | None = None,
    redirect_base_url: str | None = None,
    **client_kwargs: Any,
) -> CommonFormAuthClient | OAuthClient:
    """Accepts a display name ("Zoho Inventory") or route name ("zoho_inventory")."""
    display = IntegrationsRouteNameMapping.get(integration_type, integration_type)
    if display not in IntegrationsRouteNameMapping.values():
        raise ConfigError(f"integration type '{integration_type}' not implemented")

    selected_version, info = get_auth_client_config(display, version)
    store = store or default_store()

    if display in IntegrationsWithFormAuthOnly or info.auth_type == "form":
        return CommonFormAuthClient(display, selected_version, info, store, **client_kwargs)

    if info.auth_type == "oauth2":
        return OAuthClient(
            display,
            selected_version,
            info,
            store,
            redirect_base_url=redirect_base_url or get_settings().ui_base_url,
            **client_kwargs,
        )

    raise ConfigError(f"unhandled auth_type '{info.auth_type}' for '{display}'")


def get_shared_auth_client(
    integration_type: str, *, cache: dict[str, Any] | None = None, **kwargs: Any
) -> CommonFormAuthClient | OAuthClient:
    """OAuth clients hold pending CSRF states and a refresh lock, so the authorize and
    callback requests must see the same instance. The caller owns the cache (one per
    IntegrationsHandlers); without a cache a fresh client is returned."""
    display = IntegrationsRouteNameMapping.get(integration_type, integration_type)
    if display in IntegrationsWithFormAuthOnly or cache is None:
        return get_auth_client(display, **kwargs)
    if display not in cache:
        client = get_auth_client(display, **kwargs)
        if not isinstance(client, OAuthClient):
            raise ConfigError(f"{display}: expected an OAuth client, got {type(client).__name__}")
        cache[display] = client
    return cache[display]
