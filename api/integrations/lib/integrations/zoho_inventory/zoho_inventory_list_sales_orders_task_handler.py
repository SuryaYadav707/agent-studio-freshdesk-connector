"""List sales orders. Parameters follow GET /inventory/v1/salesorders:
salesorder_number, customer_name, customer_id, status, date_start, date_end,
search_text, filter_by, sort_column, page, per_page."""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from api.integrations.lib.integrations.task_utils import Text100, build_params
from api.integrations.lib.integrations.zoho_inventory.zoho_inventory_models import normalize_sales_order, page_context
from api.integrations.models.data_models import TaskResult

TITLE = "List Zoho Inventory sales orders"
HELPER_TEXT = (
    "List sales orders with status (draft/open/invoiced/void/closed), customer, total, shipment "
    "and invoiced/paid/shipped flags. Filter by status, customer name or id, order number, free "
    "text or a date range (YYYY-MM-DD), sort and paginate. Use zoho_inventory_get_sales_order for "
    "line items of one order."
)
DOCUMENTATION_URL = "https://www.zoho.com/inventory/api/v1/salesorders/#list-all-sales-orders"

FilterBy = Literal["Status.All", "Status.Draft", "Status.Open", "Status.Invoiced", "Status.PartiallyInvoiced", "Status.Void", "Status.Closed"]
SortColumn = Literal["customer_name", "salesorder_number", "shipment_date", "total", "date", "created_time", "last_modified_time"]


class Input(BaseModel):
    filter_by: FilterBy | None = Field(default=None, description="Predefined status filter, e.g. Status.Open.")
    status: Text100 = Field(default=None, description="Exact Zoho status value, e.g. 'open', 'draft', 'invoiced', 'void'.")
    customer_name: Text100 = Field(default=None, description="Customer (contact) display name, partial match.")
    customer_id: Text100 = Field(default=None, description="Zoho contact id of the customer.")
    salesorder_number: Text100 = Field(default=None, description="Exact order number, e.g. 'SO-00012'.")
    search_text: Text100 = Field(default=None, description="Free text across order number, reference and customer.")
    date_start: date | None = Field(default=None, description="Orders dated on/after this day (YYYY-MM-DD).")
    date_end: date | None = Field(default=None, description="Orders dated on/before this day (YYYY-MM-DD).")
    sort_column: SortColumn = "date"
    sort_order: Literal["A", "D"] = "D"
    page: int = Field(default=1, ge=1)
    per_page: int = Field(default=50, ge=1, le=200)


async def run(integration, inputs: Input) -> TaskResult:
    params = build_params(
        inputs,
        ("filter_by", "status", "customer_name", "customer_id", "salesorder_number", "search_text",
         "sort_column", "sort_order", "page", "per_page"),
        dates=("date_start", "date_end"),
    )
    resp = await integration.request("GET", "/salesorders", params=params)
    body = integration.parse_body(resp)
    orders = [normalize_sales_order(o) for o in body.get("salesorders") or []]
    page, per_page, has_more = page_context(body)
    return TaskResult(items=orders, page=page or inputs.page, per_page=per_page or inputs.per_page, has_more=has_more)
