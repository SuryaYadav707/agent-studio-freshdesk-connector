"""Generic OAuth 2.0 authorization-code client driven by integrations_config.json.
Vendor variation (Zoho data centers) comes in through the `{dc}` URL placeholder only.
"""
from __future__ import annotations

import asyncio
import logging
import secrets
import threading
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator
from urllib.parse import urlencode

import httpx

from api.integrations.config.integrations_config import VersionedIntegrationInfo
from api.integrations.lib.integrations_auth.credentials_store import CredentialsStore
from api.integrations.models.data_models import Credentials, IntegrationObject
from api.integrations.models.errors import AuthError, ConfigError, UpstreamError, ValidationError

STATE_TTL_SECONDS = 600
DEFAULT_EXPIRES_IN = 3600.0
HTTP_TIMEOUT = 20.0
log = logging.getLogger(__name__)


@asynccontextmanager
async def borrowed_client(existing: httpx.AsyncClient | None) -> AsyncIterator[httpx.AsyncClient]:
    """Use the injected client (tests) or a short-lived one closed afterwards. Shared with form auth."""
    if existing is not None:
        yield existing
        return
    client = httpx.AsyncClient(timeout=HTTP_TIMEOUT)
    try:
        yield client
    finally:
        await client.aclose()


def expires_at_from(token: dict[str, Any], *, now: float | None = None) -> float:
    """Token endpoints occasionally return a missing, zero or non-numeric expires_in.
    Treat anything that is not a positive number as one hour so we never store an
    already-expired token (which would refresh on every request)."""
    raw = token.get("expires_in", DEFAULT_EXPIRES_IN)
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        seconds = -1.0
    if seconds <= 0:
        log.warning("token endpoint returned unusable expires_in=%r, assuming %ss", raw, int(DEFAULT_EXPIRES_IN))
        seconds = DEFAULT_EXPIRES_IN
    return (now if now is not None else time.time()) + seconds


class OAuthClient:
    auth_type = "oauth2"

    def __init__(
        self,
        integration_type: str,
        version: str,
        info: VersionedIntegrationInfo,
        store: CredentialsStore,
        *,
        redirect_base_url: str,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        missing = [name for name in ("authorization_url", "token_endpoint", "redirect_endpoint") if not getattr(info, name)]
        if missing:
            raise ConfigError(f"{integration_type}: integrations_config.json is missing {', '.join(missing)}")
        self.integration_type = integration_type
        self.version = version
        self.info = info
        self.store = store
        self.redirect_uri = redirect_base_url.rstrip("/") + info.redirect_endpoint
        self._http = http_client
        self._pending_states: dict[str, float] = {}
        self._states_lock = threading.Lock()  # new_state/consume_state are sync and may be called from threads (CLI)
        self._refresh_lock = asyncio.Lock()


    def get_form_endpoint(self) -> dict[str, Any] | None:
        return None

    def new_state(self) -> str:
        """CSRF token, single use, expires after STATE_TTL_SECONDS."""
        now = time.time()
        state = secrets.token_urlsafe(24)
        with self._states_lock:
            self._pending_states = {s: t for s, t in self._pending_states.items() if now - t < STATE_TTL_SECONDS}
            self._pending_states[state] = now
        return state

    def consume_state(self, state: str | None) -> None:
        with self._states_lock:
            issued = self._pending_states.pop(state, None) if state else None
        if issued is None:
            raise AuthError(f"{self.integration_type}: OAuth state mismatch (possible CSRF or expired login)")
        if time.time() - issued >= STATE_TTL_SECONDS:
            raise AuthError(f"{self.integration_type}: OAuth state expired, start again")

    def get_authorization_url(self, _user_id: str, _org_id: str, state: str, **extra: Any) -> str:
        """Build the consent URL. `extra` may carry `scopes` (list) and `dc`."""
        dc = self._dc(extra.get("dc"))
        scopes: list[str] = extra.get("scopes") or self.info.default_scopes()
        values = {
            "client_id": self.info.require_env_field("client_id"),
            "redirect_uri": self.redirect_uri,
            "scope": self.info.scope_separator.join(scopes),
            "state": state,
        }
        query = {field: values[field] for field in self.info.authorization_url_fields if field in values}
        base = self.info.authorization_url.format(dc=dc)
        joiner = "&" if "?" in base else "?"
        return f"{base}{joiner}{urlencode(query)}"

    async def handle_auth_callback(self, request_data: dict[str, Any]) -> IntegrationObject:
        """Exchange the authorization code. request_data: code, state, optional dc / scopes."""
        if request_data.get("error"):
            raise AuthError(f"{self.integration_type}: consent denied ({request_data['error']})")
        code = request_data.get("code")
        if not code:
            raise ValidationError(f"{self.integration_type}: callback missing 'code'")
        self.consume_state(request_data.get("state"))
        dc = self._dc(request_data.get("dc"))

        payload = self._token_payload(
            self.info.token_endpoint_fields,
            {
                "client_id": self.info.require_env_field("client_id"),
                "client_secret": self.info.require_env_field("client_secret"),
                "code": code,
                "redirect_uri": self.redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        token = await self._post_token(self.info.token_endpoint.format(dc=dc), payload)
        if not token.get("access_token"):
            raise AuthError(f"{self.integration_type}: token endpoint returned no access_token ({token.get('error', 'unknown')})")

        credentials = Credentials(
            kind="oauth2",
            access_token=token["access_token"],
            refresh_token=token.get("refresh_token"),
            expires_at=expires_at_from(token),
            token_type=self.info.token_type,
            scopes=[s for s in str(token.get("scope", "")).replace(",", " ").split() if s]
            or list(request_data.get("scopes") or self.info.default_scopes()),
            extra={"dc": dc, "api_base": self.info.api_base.format(dc=dc)},
        )
        existing = self.store.load(self.integration_type)
        integration = IntegrationObject(
            id=existing.id if existing else uuid.uuid4().hex,
            integration_type=self.integration_type,
            version=self.version,
            user_id=request_data.get("user_id", "local"),
            org_id=request_data.get("org_id", "local"),
            credentials=credentials,
            additional_params={"dc": dc, **(existing.additional_params if existing else {})},
        )
        self.store.save(integration)
        return integration

    async def refresh_and_fetch_credentials(self, integration: IntegrationObject) -> Credentials:
        """Return valid credentials, refreshing under a lock so concurrent tasks share one refresh."""
        if not integration.credentials.is_expired():
            return integration.credentials
        async with self._refresh_lock:
            latest = self.store.load(self.integration_type) or integration
            if not latest.credentials.is_expired():
                integration.credentials = latest.credentials
                return latest.credentials
            return await self.force_refresh(integration)

    async def force_refresh(self, integration: IntegrationObject) -> Credentials:
        creds = integration.credentials
        if not creds.refresh_token:
            raise AuthError(f"{self.integration_type}: no refresh token stored, reconnect required")
        dc = creds.extra.get("dc") or self._dc(None)
        payload = self._token_payload(
            self.info.refresh_token_endpoint_fields,
            {
                "client_id": self.info.require_env_field("client_id"),
                "client_secret": self.info.require_env_field("client_secret"),
                "refresh_token": creds.refresh_token,
                "redirect_uri": self.redirect_uri,
                "grant_type": "refresh_token",
            },
        )
        token = await self._post_token(self.info.token_endpoint.format(dc=dc), payload)
        if not token.get("access_token"):
            raise AuthError(f"{self.integration_type}: refresh rejected ({token.get('error', 'unknown')}), reconnect required")
        creds.access_token = token["access_token"]
        creds.expires_at = expires_at_from(token)
        if self.info.token_rotation and token.get("refresh_token"):
            creds.refresh_token = token["refresh_token"]
        integration.credentials = creds
        self.store.save(integration)
        return creds

    async def revoke(self, integration: IntegrationObject) -> bool:
        """Revoke the refresh token upstream (Zoho: POST /oauth/v2/token/revoke?token=...).
        Returns False when the config has no revoke endpoint or nothing to revoke."""
        creds = integration.credentials
        token = creds.refresh_token or creds.access_token
        if not (self.info.revoke_endpoint and token):
            return False
        dc = creds.extra.get("dc") or self._dc(None)
        url = self.info.revoke_endpoint.format(dc=dc)
        try:
            async with borrowed_client(self._http) as client:
                resp = await client.post(url, params={"token": token})
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            raise UpstreamError(f"{self.integration_type}: revoke endpoint unreachable: {exc.__class__.__name__}") from exc
        if resp.status_code >= 400:
            raise UpstreamError(f"{self.integration_type}: revoke returned HTTP {resp.status_code}", status=resp.status_code)
        log.info("%s: upstream token revoked", self.integration_type)
        return True


    def _dc(self, override: str | None) -> str:
        return override or self.info.env_field("dc") or ""

    @staticmethod
    def _token_payload(fields: list[str], values: dict[str, str]) -> dict[str, str]:
        return {f: values[f] for f in fields if f in values}

    async def _post_token(self, url: str, payload: dict[str, str]) -> dict[str, Any]:
        try:
            async with borrowed_client(self._http) as client:
                resp = await client.post(url, data=payload, headers=self.info.token_endpoint_headers or None)
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            raise UpstreamError(f"{self.integration_type}: token endpoint unreachable: {exc.__class__.__name__}") from exc
        try:
            body = resp.json()
        except ValueError:
            body = {}
        if resp.status_code >= 400:
            raise AuthError(
                f"{self.integration_type}: token endpoint HTTP {resp.status_code}: {body.get('error', 'no detail')}",
                status=resp.status_code,
            )
        return body
