"""Small helpers shared by task handlers so list/search handlers stay declarative."""
from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Callable, Iterable

from pydantic import AfterValidator, BaseModel


def build_params(inputs: BaseModel, fields: Iterable[str], *, dates: Iterable[str] = (), base: dict[str, Any] | None = None) -> dict[str, Any]:
    """Copy non-empty input fields into a query-param dict.

    `fields` are passed through as-is (None / "" / [] skipped); `dates` are
    date/datetime fields rendered as ISO-8601 (dates as YYYY-MM-DD, datetimes
    as UTC `...Z`, which is what both Freshdesk and Zoho accept).
    """
    params: dict[str, Any] = dict(base or {})
    for name in fields:
        value = getattr(inputs, name, None)
        if value not in (None, "", [], ()):
            params[name] = value
    for name in dates:
        value = getattr(inputs, name, None)
        if isinstance(value, datetime):
            params[name] = value.strftime("%Y-%m-%dT%H:%M:%SZ")
        elif isinstance(value, date):
            params[name] = value.isoformat()
    return params


def trimmed_text(max_len: int) -> Callable[[str | None], str | None]:
    """Validator: strip whitespace, treat empty as None, reject over-long values."""

    def _validate(value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if len(value) > max_len:
            raise ValueError(f"text too long (max {max_len} characters)")
        return value or None

    return _validate


# Optional free-text input capped at 100 characters, used by search / filter fields.
Text100 = Annotated[str | None, AfterValidator(trimmed_text(100))]
