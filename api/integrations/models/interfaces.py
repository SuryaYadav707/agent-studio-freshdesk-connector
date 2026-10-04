"""Protocols shared by auth clients and integrations (VectorShift interfaces.go)."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

import httpx

from api.integrations.models.data_models import Credentials, IntegrationObject, RateLimitsInfo


@runtime_checkable
class IntegrationAuthClient(Protocol):
    auth_type: str  # "oauth2" | "form"

    def get_form_endpoint(self) -> dict[str, Any] | None:
        """JSON schema describing credential form fields (form auth only)."""
        ...

    def get_authorization_url(self, user_id: str, org_id: str, state: str, **extra: Any) -> str:
        """OAuth only. Form auth raises."""
        ...

    async def handle_auth_callback(self, request_data: dict[str, Any]) -> IntegrationObject:
        """Exchange code (OAuth) or validate submitted fields (form). Returns connected record."""
        ...

    async def refresh_and_fetch_credentials(self, integration: IntegrationObject) -> Credentials:
        """Return valid credentials, refreshing when expired."""
        ...


@runtime_checkable
class RequestsController(Protocol):
    async def make_request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response: ...

    def last_rate_limit_headers(self) -> dict[str, str]: ...


@runtime_checkable
class Integration(Protocol):
    integration_data: IntegrationObject

    async def get_and_set_account_name(self, use_existing_name: bool = True) -> tuple[str, str]: ...

    async def handle_task(self, task_name: str, inputs: dict[str, Any]) -> dict[str, Any]: ...

    async def check_integration_health(self) -> tuple[bool, str]: ...

    def get_rate_limits_info(self) -> RateLimitsInfo: ...

    def get_integration_data(self) -> IntegrationObject: ...

    def get_dynamic_loading(self) -> bool: ...
