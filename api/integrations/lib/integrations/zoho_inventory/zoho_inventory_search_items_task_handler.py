"""Search items by text. Uses the list endpoint's search parameters
(search_text, name_contains, sku_contains) so results share the list shape."""
from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from api.integrations.lib.integrations.task_utils import Text100, build_params
from api.integrations.lib.integrations.zoho_inventory.zoho_inventory_models import normalize_item, page_context
from api.integrations.models.data_models import TaskResult

TITLE = "Search Zoho Inventory items"
HELPER_TEXT = (
    "Find items by free text across name/SKU (search_text), or by a substring of the name or SKU. "
    "Optionally restrict to active items or to items at/below their reorder level (low_stock_only). "
    "Returns the same fields as zoho_inventory_list_items. Use this when the question names a "
    "product, SKU or keyword."
)
DOCUMENTATION_URL = "https://www.zoho.com/inventory/api/v1/items/#list-all-the-items"


class Input(BaseModel):
    search_text: Text100 = Field(default=None, description="Free text matched against name, SKU and other searchable fields.")
    name_contains: Text100 = Field(default=None, description="Substring of the item name.")
    sku_contains: Text100 = Field(default=None, description="Substring of the SKU.")
    active_only: bool = Field(default=True, description="Exclude inactive items.")
    low_stock_only: bool = Field(default=False, description="Only items whose stock is at or below the reorder level.")
    page: int = Field(default=1, ge=1)
    per_page: int = Field(default=50, ge=1, le=200)

    @model_validator(mode="after")
    def _needs_a_term(self):
        if not (self.search_text or self.name_contains or self.sku_contains or self.low_stock_only):
            raise ValueError("provide search_text, name_contains, sku_contains or low_stock_only")
        return self


async def run(integration, inputs: Input) -> TaskResult:
    params = build_params(inputs, ("search_text", "name_contains", "sku_contains", "page", "per_page"),
                          base={"sort_column": "name", "sort_order": "A"})
    if inputs.low_stock_only:
        params["filter_by"] = "Status.Lowstock"
    elif inputs.active_only:
        params["filter_by"] = "Status.Active"

    resp = await integration.request("GET", "/items", params=params)
    body = integration.parse_body(resp)
    items = [normalize_item(i) for i in body.get("items") or []]
    if inputs.low_stock_only and inputs.active_only:
        # Zoho allows one filter_by; Status.Lowstock ignores active/inactive, so apply active here.
        items = [i for i in items if i.get("status") in (None, "active")]
    page, per_page, has_more = page_context(body)
    return TaskResult(items=items, page=page or inputs.page, per_page=per_page or inputs.per_page, has_more=has_more)
