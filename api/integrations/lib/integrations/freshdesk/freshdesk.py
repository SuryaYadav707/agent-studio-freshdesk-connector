"""FreshdeskIntegration: read-only access to a Freshdesk helpdesk via API key."""
from __future__ import annotations

from api.integrations.constants.constants import FRESHDESK
from api.integrations.lib.integrations.integration_base import BaseIntegration


class FreshdeskIntegration(BaseIntegration):
    display_name = FRESHDESK
    route_name = "freshdesk"

    async def get_and_set_account_name(self, use_existing_name: bool = True) -> tuple[str, str]:
        data = self.integration_data
        if use_existing_name and data.account_id and data.account_name:
            return data.account_id, data.account_name
        resp = await self.request("GET", self.info.health_endpoint)
        me = self.parse_json(resp, what="/agents/me")
        contact = me.get("contact") or {}
        account_id = str(me.get("id", ""))
        account_name = contact.get("email") or contact.get("name") or f"agent {account_id}"
        data.account_id = account_id
        data.account_name = f"{account_name} @ {data.credentials.extra.get('domain')}.freshdesk.com"
        self.auth_client.store.save(data)
        return data.account_id, data.account_name
