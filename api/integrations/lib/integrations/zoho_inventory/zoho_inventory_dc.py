"""Zoho data-center handling.

A Zoho account lives in one data center. Both the accounts server (OAuth) and
the API host differ per DC, so the connector must use matching domains.
On the OAuth redirect Zoho appends `location=<region>` which we map to the
domain suffix used in integrations_config.json templates.
"""
from __future__ import annotations

from api.integrations.models.errors import ValidationError

# location param on callback -> domain suffix ({dc} in config templates)
LOCATION_TO_DC: dict[str, str] = {
    "us": "com",
    "eu": "eu",
    "in": "in",
    "au": "com.au",
    "jp": "jp",
    "ca": "ca",
    "cn": "com.cn",
    "sa": "sa",
}
SUPPORTED_DCS = sorted(set(LOCATION_TO_DC.values()))


def normalize_dc(value: str | None) -> str | None:
    """Accept 'in', 'com', 'us', 'IN', 'zoho.in' ... return a config suffix or None."""
    if not value:
        return None
    v = value.strip().lower().removeprefix("zoho.").removeprefix(".")
    if v in SUPPORTED_DCS:
        return v
    if v in LOCATION_TO_DC:
        return LOCATION_TO_DC[v]
    raise ValidationError(f"unsupported Zoho data center '{value}', expected one of {SUPPORTED_DCS}")


def dc_from_callback(query: dict[str, str], default: str | None) -> str:
    """Prefer Zoho's `location` param, then `accounts-server`, then the configured default."""
    if query.get("location"):
        return normalize_dc(query["location"]) or default or "com"
    server = query.get("accounts-server", "")
    for suffix in sorted(SUPPORTED_DCS, key=len, reverse=True):
        if server.endswith("accounts.zoho." + suffix):
            return suffix
    return default or "com"
