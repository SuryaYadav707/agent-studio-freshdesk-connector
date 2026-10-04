"""Form field definitions for integrations that use user-supplied credentials.

The UI renders these, and CommonFormAuthClient validates submissions against
them. Equivalent of VectorShift's form_auth_config.go.
"""
from __future__ import annotations

from typing import Any

from api.integrations.constants.constants import FRESHDESK

FORM_AUTH_FIELDS: dict[str, list[dict[str, Any]]] = {
    FRESHDESK: [
        {
            "field": "domain",
            "label": "Helpdesk subdomain",
            "type": "string",
            "required": True,
            "pattern": r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$",
            "helper_text": "The part before .freshdesk.com, e.g. 'acme-support'.",
            "env": "FRESHDESK_DOMAIN",
        },
        {
            "field": "api_key",
            "label": "API key",
            "type": "secret",
            "required": True,
            "helper_text": "Profile settings > View API key. Stored locally, never logged.",
            "env": "FRESHDESK_API_KEY",
        },
    ],
}


def get_form_fields(integration_type: str) -> list[dict[str, Any]]:
    return FORM_AUTH_FIELDS.get(integration_type, [])
