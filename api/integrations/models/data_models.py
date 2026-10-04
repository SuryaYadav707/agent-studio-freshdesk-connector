"""Data models shared across auth clients, integrations and routers.
Trimmed version of VectorShift's data_models.go."""
from __future__ import annotations

import time
from typing import Any, Literal

from pydantic import BaseModel, Field


class RateLimitsInfo(BaseModel):
    """Same shape as the `rate_limits` block in integrations_config.json."""

    per_account: int | None = None  # calls allowed per window for the whole account
    per_token: int | None = None  # calls allowed per window for this credential
    window_seconds: int = 60
    concurrency: int = 5
    cooldown_period: float = 60.0  # sleep when rate limited and no Retry-After header
    initial_sleep_period: float = 1.0
    linear_backoff_rate: float = 2.0
    rate_limit_codes: list[int] = Field(default_factory=lambda: [429])
    backoff_strategy: Literal["exponential", "linear"] = "exponential"
    max_retries: int = 3
    honor_retry_after: bool = True
    max_sleep: float = 120.0

    @property
    def calls_per_window(self) -> int | None:
        vals = [v for v in (self.per_account, self.per_token) if v]
        return min(vals) if vals else None


class Credentials(BaseModel):
    """Credential envelope. `kind` decides how the Authorization header is built."""

    kind: Literal["basic", "bearer", "oauth2"]
    username: str | None = None
    password: str | None = None
    access_token: str | None = None
    refresh_token: str | None = None
    expires_at: float | None = None  # unix epoch seconds
    token_type: str = "Bearer"  # header scheme, e.g. "Zoho-oauthtoken"
    scopes: list[str] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)

    def is_expired(self, skew_seconds: float = 60.0) -> bool:
        if self.kind != "oauth2" or self.expires_at is None:
            return False
        return time.time() + skew_seconds >= self.expires_at

    def redacted(self) -> dict[str, Any]:
        """Safe-to-log view."""
        data = self.model_dump()
        for key in ("password", "access_token", "refresh_token"):
            if data.get(key):
                data[key] = "***"
        return data


class IntegrationObject(BaseModel):
    """Persisted connection record (what VectorShift stores in Mongo)."""

    id: str
    integration_type: str  # display name, e.g. "Zoho Inventory"
    version: str = "v1"
    user_id: str = "local"
    org_id: str = "local"
    account_id: str | None = None
    account_name: str | None = None
    credentials: Credentials
    additional_params: dict[str, Any] = Field(default_factory=dict)
    connected_at: float = Field(default_factory=time.time)


class IntegrationMetadataItem(BaseModel):
    id: str
    name: str
    type: str = "item"
    parent_id: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class PaginatedResponse(BaseModel):
    items: list[dict[str, Any]]
    page: int = 1
    per_page: int | None = None
    total_count: int | None = None
    has_more: bool = False


class TaskResult(BaseModel):
    """Uniform payload returned by every task handler."""

    ok: bool = True
    data: Any = None
    items: list[dict[str, Any]] | None = None
    page: int | None = None
    per_page: int | None = None
    total_count: int | None = None
    has_more: bool | None = None
    error: dict[str, Any] | None = None
    rate_limit: dict[str, Any] | None = None  # upstream headers snapshot

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)

    @classmethod
    def failure(cls, err: Exception) -> "TaskResult":
        from api.integrations.models.errors import ConnectorError

        if isinstance(err, ConnectorError):
            return cls(ok=False, error=err.to_dict())
        return cls(ok=False, error={"type": "internal_error", "message": str(err)})
