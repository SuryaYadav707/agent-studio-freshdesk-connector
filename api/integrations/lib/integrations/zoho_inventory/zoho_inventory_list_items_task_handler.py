"""List items. Parameters follow GET /inventory/v1/items: filter_by, status,
sort_column, sort_order, page, per_page (default 200 upstream, capped lower here
to keep agent context small)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from api.integrations.lib.integrations.task_utils import build_params
from api.integrations.lib.integrations.zoho_inventory.zoho_inventory_models import normalize_item, page_context
from api.integrations.models.data_models import TaskResult

TITLE = "List Zoho Inventory items"
HELPER_TEXT = (
    "List products/items in the connected Zoho Inventory organization with stock on hand, available "
    "stock, reorder level and an is_low_stock flag. Filter by status or item type (e.g. "
    "Status.Lowstock for items at/below reorder level), sort by name, sku, rate or stock_on_hand, "
    "and paginate. To look up items by name or SKU text use zoho_inventory_search_items."
)
DOCUMENTATION_URL = "https://www.zoho.com/inventory/api/v1/items/#list-all-the-items"

FilterBy = Literal[
    "Status.All", "Status.Active", "Status.Inactive", "Status.Lowstock",
    "ItemType.All", "ItemType.Sales", "ItemType.Purchases", "ItemType.SalesAndPurchases",
    "ItemType.Inventory", "ItemType.NonInventory", "ItemType.Service",
]
SortColumn = Literal["name", "sku", "rate", "purchase_rate", "created_time", "last_modified_time", "reorder_level", "stock_on_hand"]


class Input(BaseModel):
    filter_by: FilterBy | None = Field(default=None, description="Zoho predefined filter, e.g. Status.Lowstock or ItemType.Inventory.")
    status: Literal["active", "inactive", "all"] = Field(default="active", description="Default active only; 'all' includes inactive items.")
    sort_column: SortColumn = "name"
    sort_order: Literal["A", "D"] = Field(default="A", description="A = ascending, D = descending.")
    page: int = Field(default=1, ge=1)
    per_page: int = Field(default=50, ge=1, le=200)


async def run(integration, inputs: Input) -> TaskResult:
    params = build_params(inputs, ("filter_by", "sort_column", "sort_order", "page", "per_page"))
    if inputs.status != "all":
        params["status"] = inputs.status
    resp = await integration.request("GET", "/items", params=params)
    body = integration.parse_body(resp)
    items = [normalize_item(i) for i in body.get("items") or []]
    page, per_page, has_more = page_context(body)
    return TaskResult(items=items, page=page or inputs.page, per_page=per_page or inputs.per_page, has_more=has_more)
