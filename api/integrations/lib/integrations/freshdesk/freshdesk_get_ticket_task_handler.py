from __future__ import annotations

from pydantic import BaseModel, Field

from api.integrations.lib.integrations.freshdesk.freshdesk_models import normalize_conversation, normalize_ticket
from api.integrations.models.data_models import TaskResult

TITLE = "Get Freshdesk ticket"
HELPER_TEXT = (
    "Fetch one Freshdesk ticket by its numeric id, including the description text, requester "
    "details and SLA timestamps. Optionally include the conversation thread (Freshdesk returns "
    "at most the 10 most recent replies/notes here, bodies truncated). Use after listing or "
    "searching to read a specific ticket."
)
DOCUMENTATION_URL = "https://developers.freshdesk.com/api/#view_a_ticket"


class Input(BaseModel):
    ticket_id: int = Field(description="Freshdesk ticket id (the number shown in the ticket URL).", ge=1)
    include_conversations: bool = Field(default=False, description="Also return replies and private notes.")
    include_requester: bool = True
    include_stats: bool = Field(default=True, description="Include resolved_at / first_responded_at etc.")


async def run(integration, inputs: Input) -> TaskResult:
    include = []
    if inputs.include_conversations:
        include.append("conversations")
    if inputs.include_requester:
        include.append("requester")
    if inputs.include_stats:
        include.append("stats")
    params = {"include": ",".join(include)} if include else None

    resp = await integration.request("GET", f"/tickets/{inputs.ticket_id}", params=params)
    raw = integration.parse_json(resp, what="get ticket")

    ticket = normalize_ticket(raw, include_description=True)
    if inputs.include_conversations:
        ticket["conversations"] = [normalize_conversation(c) for c in raw.get("conversations") or []]
    return TaskResult(data=ticket)
