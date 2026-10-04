"""Freshdesk enums and response normalisation.

Freshdesk returns status/priority/source as integers. The agent gets both the
code and a label so it can filter and explain without a lookup table.
"""
from __future__ import annotations

from typing import Any

STATUS_LABELS: dict[int, str] = {2: "open", 3: "pending", 4: "resolved", 5: "closed"}
PRIORITY_LABELS: dict[int, str] = {1: "low", 2: "medium", 3: "high", 4: "urgent"}
SOURCE_LABELS: dict[int, str] = {
    1: "email", 2: "portal", 3: "phone", 7: "chat", 9: "feedback_widget", 10: "outbound_email",
}
STATUS_CODES = {v: k for k, v in STATUS_LABELS.items()}
PRIORITY_CODES = {v: k for k, v in PRIORITY_LABELS.items()}

DESCRIPTION_MAX_CHARS = 2000
CONVERSATION_MAX_CHARS = 1500


def _truncate(text: str | None, limit: int) -> str | None:
    if text is None:
        return None
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + "…"


def normalize_ticket(raw: dict[str, Any], *, include_description: bool = False) -> dict[str, Any]:
    status = raw.get("status")
    priority = raw.get("priority")
    source = raw.get("source")
    ticket: dict[str, Any] = {
        "id": raw.get("id"),
        "subject": raw.get("subject"),
        "status": STATUS_LABELS.get(status, str(status)),
        "status_code": status,
        "priority": PRIORITY_LABELS.get(priority, str(priority)),
        "priority_code": priority,
        "type": raw.get("type"),
        "source": SOURCE_LABELS.get(source, str(source)),
        "tags": raw.get("tags") or [],
        "requester_id": raw.get("requester_id"),
        "responder_id": raw.get("responder_id"),
        "group_id": raw.get("group_id"),
        "is_escalated": raw.get("is_escalated"),
        "created_at": raw.get("created_at"),
        "updated_at": raw.get("updated_at"),
        "due_by": raw.get("due_by"),
        "fr_due_by": raw.get("fr_due_by"),
    }
    requester = raw.get("requester")
    if isinstance(requester, dict):
        ticket["requester"] = {k: requester.get(k) for k in ("id", "name", "email", "phone")}
    stats = raw.get("stats")
    if isinstance(stats, dict):
        ticket["stats"] = {k: stats.get(k) for k in ("resolved_at", "closed_at", "first_responded_at", "agent_responded_at")}
    if include_description:
        ticket["description_text"] = _truncate(raw.get("description_text"), DESCRIPTION_MAX_CHARS)
    custom = raw.get("custom_fields")
    if isinstance(custom, dict) and custom:
        ticket["custom_fields"] = {k: v for k, v in custom.items() if v not in (None, "", [])}
    return ticket


def normalize_conversation(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": raw.get("id"),
        "body_text": _truncate(raw.get("body_text"), CONVERSATION_MAX_CHARS),
        "incoming": raw.get("incoming"),
        "private": raw.get("private"),
        "user_id": raw.get("user_id"),
        "from_email": raw.get("from_email"),
        "created_at": raw.get("created_at"),
    }


def parse_link_header_has_next(link_header: str | None) -> bool:
    """Freshdesk signals another page with a Link header: <...page=2>; rel="next"."""
    return bool(link_header) and 'rel="next"' in link_header
