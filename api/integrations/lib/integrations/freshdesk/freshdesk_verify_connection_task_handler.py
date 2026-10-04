from __future__ import annotations

from pydantic import BaseModel

from api.integrations.models.data_models import TaskResult

TITLE = "Verify Freshdesk connection"
HELPER_TEXT = (
    "Check that the stored Freshdesk API key still works. Returns the connected agent, "
    "helpdesk domain and current rate-limit headroom. Use before other Freshdesk tools "
    "when unsure whether the connection is healthy."
)
DOCUMENTATION_URL = "https://developers.freshdesk.com/api/#authentication"


class Input(BaseModel):
    """No inputs."""


async def run(integration, _inputs: Input) -> TaskResult:
    account_id, account_name = await integration.get_and_set_account_name(use_existing_name=False)
    return TaskResult(
        data={
            "integration": "Freshdesk",
            "account_id": account_id,
            "account_name": account_name,
            "domain": integration.integration_data.credentials.extra.get("domain"),
            "auth": "api_key",
            "read_only": True,
        }
    )
