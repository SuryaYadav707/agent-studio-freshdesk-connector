"""Search tickets through Freshdesk's filter API.

The agent never writes raw filter syntax. Inputs are structured and whitelisted,
then compiled into the `"status:2 AND priority:4"` query Freshdesk expects.
Freshdesk limits the query to 512 characters and returns 30 results per page.
"""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from api.integrations.lib.integrations.freshdesk.freshdesk_models import (
    PRIORITY_CODES,
    STATUS_CODES,
    normalize_ticket,
)
from api.integrations.models.data_models import TaskResult
from api.integrations.models.errors import ValidationError

TITLE = "Search Freshdesk tickets"
HELPER_TEXT = (
    "Filter Freshdesk tickets by status, priority, tag, type, assigned agent/group and "
    "created/updated date ranges. All conditions are combined with AND; multiple values for "
    "one field are OR-ed. Returns 30 tickets per page with a total count. Use this instead of "
    "freshdesk_list_tickets whenever the question mentions status, priority, tags or agents."
)
DOCUMENTATION_URL = "https://developers.freshdesk.com/api/#filter_tickets"

QUERY_MAX_CHARS = 512
PER_PAGE = 30  # fixed by Freshdesk for the filter endpoint

StatusName = Literal["open", "pending", "resolved", "closed"]
PriorityName = Literal["low", "medium", "high", "urgent"]


class Input(BaseModel):
    status: list[StatusName] | None = Field(default=None, description="One or more of open, pending, resolved, closed.")
    priority: list[PriorityName] | None = Field(default=None, description="One or more of low, medium, high, urgent.")
    tags: list[str] | None = Field(default=None, description="Tickets carrying any of these tags.")
    ticket_type: str | None = Field(default=None, description="Freshdesk ticket type, e.g. 'Question', 'Incident'.")
    agent_id: int | None = Field(default=None, description="Assigned agent id.")
    group_id: int | None = Field(default=None, description="Assigned group id.")
    created_after: date | None = Field(default=None, description="Tickets created on/after this date (YYYY-MM-DD).")
    created_before: date | None = Field(default=None, description="Tickets created on/before this date (YYYY-MM-DD).")
    updated_after: date | None = Field(default=None, description="Tickets updated on/after this date (YYYY-MM-DD).")
    page: int = Field(default=1, ge=1, le=10, description="Freshdesk allows up to 10 pages of 30 results.")

    @field_validator("tags", "ticket_type")
    @classmethod
    def _safe_text(cls, value):
        values = value if isinstance(value, list) else [value]
        for v in values:
            if v is None:
                continue
            if any(ch in v for ch in "\"'()\\:"):
                raise ValueError("must not contain quotes, parentheses, backslashes or colons")
            if len(v) > 50:
                raise ValueError("too long (max 50 characters)")
        return value


def _or_group(field: str, values: list[str]) -> str:
    parts = [f"{field}:{v}" for v in values]
    return parts[0] if len(parts) == 1 else "(" + " OR ".join(parts) + ")"


# (input field, clause builder). Order defines clause order in the query.
CLAUSES = (
    ("status", lambda v: _or_group("status", [str(STATUS_CODES[s]) for s in v])),
    ("priority", lambda v: _or_group("priority", [str(PRIORITY_CODES[p]) for p in v])),
    ("tags", lambda v: _or_group("tag", [f"'{t}'" for t in v])),
    ("ticket_type", lambda v: f"type:'{v}'"),
    ("agent_id", lambda v: f"agent_id:{v}"),
    ("group_id", lambda v: f"group_id:{v}"),
    ("created_after", lambda v: f"created_at:>'{v.isoformat()}'"),
    ("created_before", lambda v: f"created_at:<'{v.isoformat()}'"),
    ("updated_after", lambda v: f"updated_at:>'{v.isoformat()}'"),
)


def build_query(inputs: Input) -> str:
    clauses = [build(value) for field, build in CLAUSES if (value := getattr(inputs, field)) not in (None, [], "")]
    if not clauses:
        raise ValidationError("search needs at least one filter (status, priority, tags, dates, ...)")
    query = " AND ".join(clauses)
    if len(query) > QUERY_MAX_CHARS:
        raise ValidationError(f"search query exceeds Freshdesk's {QUERY_MAX_CHARS} character limit")
    return query


async def run(integration, inputs: Input) -> TaskResult:
    query = build_query(inputs)
    # Freshdesk requires the whole expression wrapped in double quotes.
    resp = await integration.request("GET", "/search/tickets", params={"query": f'"{query}"', "page": inputs.page})
    raw = integration.parse_json(resp, what="search tickets")

    results = raw.get("results") or []
    total = raw.get("total")
    items = [normalize_ticket(t) for t in results]
    has_more = total is not None and inputs.page * PER_PAGE < total and inputs.page < 10
    return TaskResult(items=items, page=inputs.page, per_page=PER_PAGE, total_count=total, has_more=has_more,
                      data={"query": query})
