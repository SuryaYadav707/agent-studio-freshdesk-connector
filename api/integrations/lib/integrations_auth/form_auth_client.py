"""CommonFormAuthClient: user-supplied credentials (API key) path.

Flow (VectorShift form_auth_client.go):
  1. get_form_endpoint() -> field schema for the UI
  2. user submits fields -> handle_auth_callback() validates shape, builds
     Credentials, makes one live call to the integration's health endpoint,
     stores the IntegrationObject on success.
  3. refresh_and_fetch_credentials() is a no-op: keys do not expire.
"""
from __future__ import annotations

import re
import time
import uuid
from typing import Any

import httpx

from api.integrations.config.form_auth_config import get_form_fields
from api.integrations.config.integrations_config import VersionedIntegrationInfo
from api.integrations.lib.integrations_auth.credentials_store import CredentialsStore
from api.integrations.lib.integrations_auth.oauth_client import borrowed_client
from api.integrations.models.data_models import Credentials, IntegrationObject
from api.integrations.models.errors import AuthError, UpstreamError, ValidationError


class CommonFormAuthClient:
    auth_type = "form"

    def __init__(
        self,
        integration_type: str,
        version: str,
        info: VersionedIntegrationInfo,
        store: CredentialsStore,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.integration_type = integration_type
        self.version = version
        self.info = info
        self.store = store
        self._http = http_client


    def get_form_endpoint(self) -> dict[str, Any]:
        return {"integration": self.integration_type, "fields": get_form_fields(self.integration_type)}

    def get_authorization_url(self, _user_id: str, _org_id: str, _state: str, **_extra: Any) -> str:
        raise ValidationError(f"{self.integration_type} uses form (API key) authentication, not OAuth")

    async def handle_auth_callback(self, request_data: dict[str, Any]) -> IntegrationObject:
        fields = self._validate_fields(request_data)
        credentials = self.build_credentials(fields)
        await self._verify_live(credentials)
        existing = self.store.load(self.integration_type)  # reconnecting keeps the same record id
        integration = IntegrationObject(
            id=existing.id if existing else uuid.uuid4().hex,
            integration_type=self.integration_type,
            version=self.version,
            user_id=request_data.get("user_id", "local"),
            org_id=request_data.get("org_id", "local"),
            credentials=credentials,
            connected_at=existing.connected_at if existing else time.time(),
        )
        self.store.save(integration)
        return integration

    async def refresh_and_fetch_credentials(self, integration: IntegrationObject) -> Credentials:
        return integration.credentials


    def _validate_fields(self, data: dict[str, Any]) -> dict[str, str]:
        out: dict[str, str] = {}
        for spec in get_form_fields(self.integration_type):
            name = spec["field"]
            value = (data.get(name) or "").strip()
            if spec.get("required") and not value:
                raise ValidationError(f"'{spec['label']}' is required")
            pattern = spec.get("pattern")
            if value and pattern and not re.match(pattern, value):
                raise ValidationError(f"'{spec['label']}' has an invalid format")
            out[name] = value
        return out

    def build_credentials(self, fields: dict[str, str]) -> Credentials:
        """Basic auth with the key as username. Freshdesk ignores the password ("X")."""
        domain = fields["domain"]
        api_base = self.info.api_base.format(domain=domain)
        return Credentials(
            kind="basic",
            username=fields["api_key"],
            password="X",
            extra={"domain": domain, "api_base": api_base},
        )

    async def _verify_live(self, credentials: Credentials) -> None:
        url = credentials.extra["api_base"] + self.info.health_endpoint
        auth = (credentials.username or "", credentials.password or "")
        try:
            async with borrowed_client(self._http) as client:
                resp = await client.get(url, auth=auth)
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            raise UpstreamError(f"{self.integration_type}: could not reach {credentials.extra['domain']}: {exc.__class__.__name__}") from exc
        if resp.status_code in (401, 403):
            raise AuthError(f"{self.integration_type}: API key rejected", status=resp.status_code)
        if resp.status_code == 404:
            raise AuthError(f"{self.integration_type}: helpdesk domain not found", status=404)
        if resp.status_code >= 400:
            raise UpstreamError(f"{self.integration_type}: validation call failed with HTTP {resp.status_code}", status=resp.status_code)
