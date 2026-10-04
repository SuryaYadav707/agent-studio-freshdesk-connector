"""Integration factory (VectorShift integrations_utils.go).

Integration classes register themselves in `INTEGRATION_CLASSES` when their
module is imported (BaseIntegration.__init_subclass__). Adding an integration
means adding a module here and nothing else in this file.
"""
from __future__ import annotations

import importlib
from typing import Any

import httpx

from api.integrations.config.integrations_config import get_current_integration_version
from api.integrations.constants.constants import IntegrationsDisplayToRoute, IntegrationsRouteNameMapping
from api.integrations.lib.integrations.integration_base import INTEGRATION_CLASSES, BaseIntegration
from api.integrations.lib.integrations_auth.auth_client import default_store, get_shared_auth_client
from api.integrations.lib.integrations_auth.credentials_store import CredentialsStore
from api.integrations.models.data_models import IntegrationObject
from api.integrations.models.errors import AuthError, ConfigError

# Explicit list (no filesystem scan) so a missing module is an import error at startup, not at call time.
INTEGRATION_MODULES = (
    "api.integrations.lib.integrations.freshdesk.freshdesk",
    "api.integrations.lib.integrations.zoho_inventory.zoho_inventory",
)
for _module in INTEGRATION_MODULES:
    importlib.import_module(_module)


def get_integration_class(integration_type: str) -> type[BaseIntegration]:
    """Accepts a route or display name."""
    route = IntegrationsDisplayToRoute.get(integration_type, integration_type)
    try:
        return INTEGRATION_CLASSES[route]
    except KeyError:
        raise ConfigError(f"integration type '{integration_type}' not implemented") from None


def get_integration_instance_from_data(
    integration_data: IntegrationObject,
    *,
    store: CredentialsStore | None = None,
    http_client: httpx.AsyncClient | None = None,
    auth_cache: dict[str, Any] | None = None,
    **auth_kwargs: Any,
) -> BaseIntegration:
    integration_type = integration_data.integration_type
    cls = get_integration_class(integration_type)
    _, info = get_current_integration_version(integration_type, integration_data.version)
    auth_client = get_shared_auth_client(integration_type, store=store, cache=auth_cache, **auth_kwargs)
    return cls(integration_data, auth_client, info, http_client=http_client)


def get_integration_instance_from_type(
    integration_type: str,
    *,
    store: CredentialsStore | None = None,
    http_client: httpx.AsyncClient | None = None,
    auth_cache: dict[str, Any] | None = None,
    **auth_kwargs: Any,
) -> BaseIntegration:
    """Raises AuthError when nothing is connected yet."""
    display = IntegrationsRouteNameMapping.get(integration_type, integration_type)
    store = store or default_store()
    integration_data = store.load(display)
    if integration_data is None:
        raise AuthError(f"{display} is not connected yet. Connect it in the UI or run `connector auth`.")
    return get_integration_instance_from_data(
        integration_data, store=store, http_client=http_client, auth_cache=auth_cache, **auth_kwargs
    )
