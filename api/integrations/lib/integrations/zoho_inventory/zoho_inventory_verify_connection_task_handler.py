"""Health check tool. Also the step that binds the connection to an organization
after OAuth consent (GET /organizations, pick one, persist organization_id)."""
from __future__ import annotations

from pydantic import BaseModel

from api.integrations.models.data_models import TaskResult

TITLE = "Verify Zoho Inventory connection"
HELPER_TEXT = (
    "Check that the Zoho Inventory OAuth connection is valid (refreshing the access token if "
    "needed) and report the organization the agent is bound to, the data center and the granted "
    "scopes. Use before other Zoho tools when unsure whether the connection is healthy, or when "
    "another Zoho tool returned auth_error or config_error."
)
DOCUMENTATION_URL = "https://www.zoho.com/inventory/api/v1/oauth/"


class Input(BaseModel):
    """No inputs."""


async def run(integration, _inputs: Input) -> TaskResult:
    org_id, org_name = await integration.get_and_set_account_name(use_existing_name=False)
    creds = integration.integration_data.credentials
    return TaskResult(
        data={
            "integration": "Zoho Inventory",
            "organization_id": org_id,
            "organization_name": org_name,
            "dc": creds.extra.get("dc"),
            "api_base": creds.extra.get("api_base"),
            "scopes": creds.scopes,
            "auth": "oauth2",
            "read_only": True,
        }
    )
