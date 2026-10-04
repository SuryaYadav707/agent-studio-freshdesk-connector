from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from api.integrations.lib.integrations.zoho_inventory.zoho_inventory_models import normalize_sales_order
from api.integrations.models.data_models import TaskResult

TITLE = "Get Zoho Inventory sales order"
HELPER_TEXT = (
    "Fetch one sales order by salesorder_id with line items (item, SKU, quantity ordered / invoiced "
    "/ shipped, rate), totals, addresses and notes. Use after listing orders to answer questions "
    "about a specific order."
)
DOCUMENTATION_URL = "https://www.zoho.com/inventory/api/v1/salesorders/#retrieve-a-sales-order"


class Input(BaseModel):
    salesorder_id: str = Field(description="Zoho salesorder_id (numeric string). Not the SO-00012 number; list orders to map it.")

    @field_validator("salesorder_id")
    @classmethod
    def _digits(cls, value: str) -> str:
        value = value.strip()
        if not value.isdigit():
            raise ValueError("salesorder_id must be the numeric Zoho id")
        return value


async def run(integration, inputs: Input) -> TaskResult:
    resp = await integration.request("GET", f"/salesorders/{inputs.salesorder_id}")
    body = integration.parse_body(resp)
    return TaskResult(data=normalize_sales_order(body.get("salesorder") or {}, detail=True))
