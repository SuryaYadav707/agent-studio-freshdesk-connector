"""List tickets. Parameters follow GET /api/v2/tickets exactly:
filter, requester_id, email, company_id, updated_since, include, order_by,
order_type, per_page (max 100), page (max 300). Next page is signalled by the
`Link` header.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field

from api.integrations.lib.integrations.freshdesk.freshdesk_models import normalize_ticket, parse_link_header_has_next
from api.integrations.lib.integrations.task_utils import build_params
from api.integrations.models.data_models import TaskResult
from api.integrations.models.errors import UpstreamError

TITLE = "List Freshdesk tickets"
HELPER_TEXT = (
    "List tickets from the connected Freshdesk helpdesk, newest first by default. "
    "Supports Freshdesk's predefined views (new_and_my_open, watching, spam, deleted), "
    "filtering by requester id / email / company id, an updated_since cutoff and pagination "
    "(max 100 per page, up to page 300). Results carry status and priority labels. "
    "To filter by status, priority, tag, agent or date range use freshdesk_search_tickets."
)
DOCUMENTATION_URL = "https://developers.freshdesk.com/api/#list_all_tickets"
PER_PAGE_MAX = 100  # Freshdesk hard limits
PAGE_MAX = 300


class Input(BaseModel):
    filter: Literal["new_and_my_open", "watching", "spam", "deleted"] | None = Field(
        default=None, description="Predefined Freshdesk view. Omit for all non-deleted, non-spam tickets."
    )
    requester_id: int | None = Field(default=None, description="Only tickets raised by this requester id.")
    email: EmailStr | None = Field(default=None, description="Only tickets raised by this requester email.")
    company_id: int | None = Field(default=None, description="Only tickets from this company id.")
    updated_since: datetime | None = Field(default=None, description="Only tickets updated after this ISO-8601 timestamp.")
    order_by: Literal["created_at", "due_by", "updated_at", "status"] = "created_at"
    order_type: Literal["asc", "desc"] = "desc"
    page: int = Field(default=1, ge=1, le=PAGE_MAX)
    per_page: int = Field(default=30, ge=1, le=PER_PAGE_MAX)
    include_requester: bool = Field(default=True, description="Embed requester name/email on each ticket.")
    include_stats: bool = Field(default=False, description="Embed resolved_at / first_responded_at timestamps.")
    include_description: bool = Field(default=False, description="Include the ticket body text (truncated).")


async def run(integration, inputs: Input) -> TaskResult:
    params = build_params(
        inputs,
        ("filter", "requester_id", "email", "company_id", "order_by", "order_type", "page", "per_page"),
        dates=("updated_since",),
    )
    if inputs.email:
        params["email"] = str(inputs.email)
    include = [name for name, on in (("requester", inputs.include_requester), ("stats", inputs.include_stats),
                                     ("description", inputs.include_description)) if on]
    if include:
        params["include"] = ",".join(include)

    resp = await integration.request("GET", "/tickets", params=params)
    raw = integration.parse_json(resp, what="list tickets")
    if not isinstance(raw, list):
        raise UpstreamError("Freshdesk: unexpected list tickets payload")

    items = [normalize_ticket(t, include_description=inputs.include_description) for t in raw]
    return TaskResult(items=items, page=inputs.page, per_page=inputs.per_page,
                      has_more=parse_link_header_has_next(resp.headers.get("Link")))
