"""ZohoInventoryIntegration: read-only access to a Zoho Inventory organization via OAuth 2.0.

Zoho specifics handled here:
  * every request carries `organization_id` as a query parameter
  * rate limits come back as HTTP 429 with a body code: 44 per-minute (retry after
    cooldown), 45 daily quota (fail fast), 1070 concurrency (short retry)
  * the organization is discovered after consent via GET /organizations
"""
from __future__ import annotations

from typing import Any

import httpx

from api.integrations.config.integrations_config import VersionedIntegrationInfo
from api.integrations.constants.constants import ZOHO_INVENTORY
from api.integrations.lib.integrations.integration_base import BaseIntegration
from api.integrations.lib.integrations.requests_controller import RateLimitDecision
from api.integrations.lib.integrations.zoho_inventory.zoho_inventory_dc import dc_from_callback, normalize_dc
from api.integrations.lib.integrations.zoho_inventory.zoho_inventory_models import normalize_organization
from api.integrations.models.errors import ConfigError, UpstreamError


def _as_seconds(raw: str | None, *, default: float) -> float:
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


ZOHO_PER_MINUTE_CODE = 44
ZOHO_DAILY_CODE = 45
ZOHO_CONCURRENCY_CODE = 1070


class ZohoInventoryIntegration(BaseIntegration):
    display_name = ZOHO_INVENTORY
    route_name = "zoho_inventory"


    @staticmethod
    def rate_limit_response_hook(resp: httpx.Response) -> RateLimitDecision | None:
        if resp.status_code != 429:
            return None
        try:
            code = int((resp.json() or {}).get("code", 0))
        except (ValueError, AttributeError):
            code = 0
        if code == ZOHO_DAILY_CODE:
            return RateLimitDecision(retry=False, wait_seconds=3600,
                                     message="daily API quota for this organization is exhausted (Zoho code 45)")
        if code == ZOHO_CONCURRENCY_CODE:
            return RateLimitDecision(retry=True, wait_seconds=3, message="too many concurrent requests (Zoho code 1070)")
        # code 44 or an unlabelled 429: per-minute window, honour Retry-After or cooldown
        wait = _as_seconds(resp.headers.get("Retry-After"), default=60.0)
        return RateLimitDecision(retry=True, wait_seconds=wait, message="per-minute limit exceeded (Zoho code 44)")

    response_hook = rate_limit_response_hook  # picked up by BaseIntegration.__init__

    @classmethod
    def prepare_callback_data(cls, data: dict[str, Any], info: VersionedIntegrationInfo) -> dict[str, Any]:
        """Zoho appends `location` / `accounts-server` to the redirect; map them to the DC suffix."""
        data = dict(data)
        data["dc"] = dc_from_callback(data, normalize_dc(data.get("dc")) or info.env_field("dc"))
        return data


    @property
    def organization_id(self) -> str | None:
        return self.integration_data.additional_params.get("organization_id") or self.info.env_field("organization_id")

    def require_organization_id(self) -> str:
        org = self.organization_id
        if not org:
            raise ConfigError("Zoho Inventory: no organization selected. Run verify_connection or set ZOHO_ORGANIZATION_ID.")
        return org

    async def request(self, method: str, path: str, *, params: dict[str, Any] | None = None, **kwargs: Any) -> httpx.Response:
        params = dict(params or {})
        if "organization_id" not in params and not path.startswith("/organizations"):
            params["organization_id"] = self.require_organization_id()
        return await super().request(method, path, params=params, **kwargs)

    def parse_body(self, resp: httpx.Response, key: str | None = None) -> Any:
        """Zoho wraps errors in a 200 body: {"code": <nonzero>, "message": ...}."""
        body = self.parse_json(resp)
        if isinstance(body, dict) and body.get("code") not in (None, 0):
            raise UpstreamError(f"Zoho Inventory: {body.get('message', 'error')} (code {body.get('code')})", status=resp.status_code)
        return body.get(key) if key else body

    async def list_organizations(self) -> list[dict[str, Any]]:
        resp = await self.request("GET", "/organizations")
        orgs = self.parse_body(resp, "organizations") or []
        return [normalize_organization(o) for o in orgs]

    async def get_and_set_account_name(self, use_existing_name: bool = True) -> tuple[str, str]:
        data = self.integration_data
        if use_existing_name and data.account_id and data.account_name:
            return data.account_id, data.account_name

        orgs = await self.list_organizations()
        if not orgs:
            raise ConfigError("Zoho Inventory: this Zoho account has no Inventory organization yet. Create one at inventory.zoho.com.")
        wanted = self.organization_id
        chosen = None
        if wanted:
            chosen = next((o for o in orgs if o["organization_id"] == str(wanted)), None)
            if chosen is None:
                raise ConfigError(f"Zoho Inventory: organization {wanted} not found in this account. Available: "
                                  + ", ".join(f"{o['organization_id']} ({o['name']})" for o in orgs))
        elif len(orgs) == 1:
            chosen = orgs[0]
        else:
            default = next((o for o in orgs if o.get("is_default_org")), None)
            if default is None:
                raise ConfigError("Zoho Inventory: multiple organizations found, set ZOHO_ORGANIZATION_ID to one of: "
                                  + ", ".join(f"{o['organization_id']} ({o['name']})" for o in orgs))
            chosen = default

        data.account_id = chosen["organization_id"]
        data.account_name = f"{chosen['name']} (org {chosen['organization_id']}, zoho.{data.credentials.extra.get('dc')})"
        data.additional_params["organization_id"] = chosen["organization_id"]
        self.auth_client.store.save(data)
        return data.account_id, data.account_name
