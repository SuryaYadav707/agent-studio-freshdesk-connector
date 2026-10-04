"""Handlers shared by the MCP server, demo UI and CLI: every auth step and task run goes through here."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from api.integrations.config.integrations_config import get_integration_info
from api.integrations.config.settings import get_settings
from api.integrations.constants.constants import (
    INTEGRATION_FUNCTIONS,
    IntegrationsRouteNameMapping,
    IntegrationsWithAdditionalParams,
    IntegrationsWithFormAuthOnly,
    parse_task_name,
)
from api.integrations.lib.integrations.integration_base import BaseIntegration
from api.integrations.lib.integrations.integrations_utils import (
    get_integration_class,
    get_integration_instance_from_type,
)
from api.integrations.lib.integrations.zoho_inventory.zoho_inventory_dc import normalize_dc
from api.integrations.lib.integrations_auth.auth_client import default_store, get_shared_auth_client
from api.integrations.lib.integrations_auth.credentials_store import CredentialsStore
from api.integrations.lib.integrations_auth.oauth_client import OAuthClient
from api.integrations.models.data_models import IntegrationObject
from api.integrations.models.errors import AuthError, ConfigError, ConnectorError, ValidationError

log = logging.getLogger(__name__)


class IntegrationsHandlers:
    def __init__(self, store: CredentialsStore | None = None, *, enabled: list[str] | None = None) -> None:
        self.store = store or default_store()
        self.enabled = enabled or get_settings().connector_integrations
        self._instances: dict[str, BaseIntegration] = {}
        self._auth_clients: dict[str, Any] = {}  # OAuth clients (CSRF states, refresh lock), one per integration
        self._lock = asyncio.Lock()


    def _display(self, route: str) -> str:
        if route not in IntegrationsRouteNameMapping:
            raise ValidationError(f"unknown integration '{route}'")
        if route not in self.enabled:
            raise ConfigError(f"integration '{route}' is disabled (CONNECTOR_INTEGRATIONS)")
        return IntegrationsRouteNameMapping[route]

    async def get_integration(self, route: str, *, fresh: bool = False) -> BaseIntegration:
        self._display(route)
        async with self._lock:
            if fresh or route not in self._instances:
                self._instances[route] = get_integration_instance_from_type(
                    route, store=self.store, auth_cache=self._auth_clients
                )
            return self._instances[route]

    def _auth_client(self, route: str):
        return get_shared_auth_client(self._display(route), store=self.store, cache=self._auth_clients)

    def invalidate(self, route: str) -> None:
        self._instances.pop(route, None)


    def tasks(self) -> list[tuple[str, str]]:
        """(route, task) pairs for enabled integrations, in registry order."""
        out = []
        for fn in INTEGRATION_FUNCTIONS:
            route, task = parse_task_name(fn)
            if route in self.enabled:
                out.append((route, task))
        return out

    async def handle_task(self, route: str, task: str, inputs: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            integration = await self.get_integration(route)
        except (AuthError, ConfigError, ValidationError) as exc:
            return {"ok": False, "error": exc.to_dict()}
        result = await integration.handle_task(task, inputs or {})
        if not result.get("ok") and result.get("error", {}).get("type") == "auth_error":
            # Credentials may have been replaced by the UI/CLI in another process: reload once.
            log.info("%s.%s got auth_error, reloading credentials and retrying once", route, task)
            integration = await self.get_integration(route, fresh=True)
            result = await integration.handle_task(task, inputs or {})
            if not result.get("ok"):
                log.warning("%s.%s still failing after credential reload: %s", route, task, result.get("error"))
        return result


    def auth_kind(self, route: str) -> str:
        display = self._display(route)
        return "form" if display in IntegrationsWithFormAuthOnly else "oauth2"

    def get_form(self, route: str) -> dict[str, Any] | None:
        return self._auth_client(route).get_form_endpoint()

    def get_authorization_url(self, route: str, *, actions: list[str] | None = None, dc: str | None = None) -> tuple[str, str]:
        """Returns (url, state). `actions` widens scopes via action_scope_map (used by the seed script)."""
        display = self._display(route)
        client = self._auth_client(route)
        if not isinstance(client, OAuthClient):
            raise ValidationError(f"{display} does not use OAuth")
        info = get_integration_info(display)
        scopes = info.scopes_for_actions(actions or [])
        state = client.new_state()
        url = client.get_authorization_url("local", "local", state, scopes=scopes, dc=normalize_dc(dc))
        return url, state

    async def handle_callback(self, route: str, request_data: dict[str, Any]) -> IntegrationObject:
        """Form submit (API key) or OAuth redirect query. Verifies the connection afterwards."""
        display = self._display(route)
        client = self._auth_client(route)
        info = get_integration_info(display)
        # vendor-specific normalisation (e.g. Zoho data center) lives on the integration class
        data = get_integration_class(route).prepare_callback_data(dict(request_data), info)
        connected = await client.handle_auth_callback(data)
        log.info("%s connected (record %s), verifying", display, connected.id)
        self.invalidate(route)
        integration = await self.get_integration(route, fresh=True)
        ok, message = await integration.check_integration_health()
        if not ok:
            self.store.delete(display)
            self.invalidate(route)
            raise AuthError(f"{display}: connected but verification failed: {message}")
        return integration.get_integration_data()

    async def health(self, route: str) -> dict[str, Any]:
        display = self._display(route)
        stored = self.store.load(display)
        if stored is None:
            return {"integration": route, "connected": False, "auth": self.auth_kind(route)}
        integration = await self.get_integration(route)
        ok, message = await integration.check_integration_health()
        return {
            "integration": route,
            "connected": ok,
            "auth": self.auth_kind(route),
            "account_name": stored.account_name,
            "message": None if ok else message,
            "rate_limit": integration.requests_controller.last_rate_limit_headers() or None,
        }

    async def disconnect(self, route: str) -> dict[str, Any]:
        """Kill switch: revoke the OAuth grant upstream (best effort), then forget the credentials."""
        display = self._display(route)
        stored = self.store.load(display)
        revoked = False
        client = self._auth_client(route)
        if stored is not None and isinstance(client, OAuthClient):
            try:
                revoked = await client.revoke(stored)
            except ConnectorError as exc:  # upstream unreachable: still delete locally
                log.warning("%s: revoke failed, deleting local credentials anyway: %s", display, exc.message)
        self.store.delete(display)
        self.invalidate(route)
        return {"integration": route, "revoked_upstream": revoked}

    def connections(self) -> list[dict[str, Any]]:
        out = []
        for route in self.enabled:
            display = IntegrationsRouteNameMapping[route]
            stored = self.store.load(display)
            extra_params = {
                name: stored.additional_params.get(name) or stored.credentials.extra.get(name)
                for name in IntegrationsWithAdditionalParams.get(display, [])
            } if stored else {}
            out.append({
                "integration": route,
                "display_name": display,
                "auth": self.auth_kind(route),
                "connected": stored is not None,
                "account_name": stored.account_name if stored else None,
                "connected_at": stored.connected_at if stored else None,
                "scopes": stored.credentials.scopes if stored else [],
                "additional_params": {k: v for k, v in extra_params.items() if v},
            })
        return out
