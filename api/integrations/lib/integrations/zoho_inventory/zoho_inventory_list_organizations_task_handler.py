from __future__ import annotations

from pydantic import BaseModel

from api.integrations.models.data_models import TaskResult

TITLE = "List Zoho Inventory organizations"
HELPER_TEXT = (
    "List the Zoho Inventory organizations the connected Zoho account can access, with their ids, "
    "currency and plan. The connector works against one organization at a time; this shows which "
    "exist. Does not require an organization to be selected yet."
)
DOCUMENTATION_URL = "https://www.zoho.com/inventory/api/v1/organizations/"


class Input(BaseModel):
    """No inputs."""


async def run(integration, _inputs: Input) -> TaskResult:
    orgs = await integration.list_organizations()
    return TaskResult(items=orgs, total_count=len(orgs), has_more=False,
                      data={"selected_organization_id": integration.organization_id})
