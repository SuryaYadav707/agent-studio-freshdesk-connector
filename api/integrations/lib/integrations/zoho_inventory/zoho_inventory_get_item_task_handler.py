from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from api.integrations.lib.integrations.zoho_inventory.zoho_inventory_models import normalize_item
from api.integrations.models.data_models import TaskResult

TITLE = "Get Zoho Inventory item"
HELPER_TEXT = (
    "Fetch one Zoho Inventory item by item_id with full detail: description, pricing, stock on hand "
    "and available stock per location/warehouse, reorder level. Use after listing or searching to "
    "answer questions about a specific product."
)
DOCUMENTATION_URL = "https://www.zoho.com/inventory/api/v1/items/#retrieve-an-item"


class Input(BaseModel):
    item_id: str = Field(description="Zoho item_id (numeric string, e.g. '4815000000044220').")

    @field_validator("item_id")
    @classmethod
    def _digits(cls, value: str) -> str:
        value = value.strip()
        if not value.isdigit():
            raise ValueError("item_id must be the numeric Zoho id")
        return value


async def run(integration, inputs: Input) -> TaskResult:
    resp = await integration.request("GET", f"/items/{inputs.item_id}")
    body = integration.parse_body(resp)
    return TaskResult(data=normalize_item(body.get("item") or {}, detail=True))
