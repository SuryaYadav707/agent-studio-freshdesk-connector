"""BaseIntegration: RequestsController wiring, auth header from stored Credentials, and
handle_task() dispatch to `<route>_<task>_task_handler.py` modules.

Handler contract: TITLE, HELPER_TEXT (MCP tool description), DOCUMENTATION_URL,
class Input(BaseModel) (MCP inputSchema), async run(integration, inputs) -> TaskResult.
"""
from __future__ import annotations

import base64
import importlib
import logging
from types import ModuleType
from typing import Any

import httpx
from pydantic import ValidationError as PydanticValidationError

from api.integrations.config.integrations_config import VersionedIntegrationInfo
from api.integrations.constants.constants import IntegrationsDisplayToRoute, tasks_for_integration
from api.integrations.lib.integrations.requests_controller import RequestsController, ResponseHook
from api.integrations.lib.integrations_auth.form_auth_client import CommonFormAuthClient
from api.integrations.lib.integrations_auth.oauth_client import OAuthClient
from api.integrations.models.data_models import IntegrationObject, RateLimitsInfo, TaskResult
from api.integrations.models.errors import AuthError, ConfigError, ConnectorError, UpstreamError, ValidationError

log = logging.getLogger(__name__)


# route_name -> integration class, filled by BaseIntegration.__init_subclass__ when the
# integration modules are imported (see integrations_utils.INTEGRATION_MODULES).
INTEGRATION_CLASSES: dict[str, type["BaseIntegration"]] = {}


def load_task_handler(route_name: str, task: str) -> ModuleType:
    if task not in tasks_for_integration(route_name):
        raise ValidationError(f"unsupported task '{task}' for integration '{route_name}'")
    module_name = f"api.integrations.lib.integrations.{route_name}.{route_name}_{task}_task_handler"
    try:
        return importlib.import_module(module_name)
    except ImportError as exc:  # handler listed in constants but file missing
        raise ValidationError(f"task handler not implemented: {module_name}") from exc


class BaseIntegration:
    display_name: str = ""
    route_name: str = ""
    # Optional per-integration hook that classifies vendor-specific rate-limit responses.
    response_hook: ResponseHook | None = None

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if cls.route_name:
            INTEGRATION_CLASSES[cls.route_name] = cls

    @classmethod
    def prepare_callback_data(cls, data: dict[str, Any], info: VersionedIntegrationInfo) -> dict[str, Any]:  # noqa: ARG003
        """Hook for vendor-specific normalisation of auth callback data (e.g. Zoho's data-center
        parameter) before the generic auth client handles it. Default: unchanged."""
        return data

    def __init__(
        self,
        integration_data: IntegrationObject,
        auth_client: CommonFormAuthClient | OAuthClient,
        info: VersionedIntegrationInfo,
        *,
        http_client: httpx.AsyncClient | None = None,
        response_hook: ResponseHook | None = None,
    ) -> None:
        self.integration_data = integration_data
        self.auth_client = auth_client
        self.info = info
        self.route_name = self.route_name or IntegrationsDisplayToRoute[integration_data.integration_type]
        self.display_name = self.display_name or integration_data.integration_type
        self.requests_controller = RequestsController(
            self.display_name,
            info.rate_limits,
            self._auth_headers,
            on_auth_failure=self._on_auth_failure if isinstance(auth_client, OAuthClient) else None,
            response_hook=response_hook or type(self).response_hook,
            client=http_client,
        )


    async def _auth_headers(self) -> dict[str, str]:
        creds = await self.auth_client.refresh_and_fetch_credentials(self.integration_data)
        if creds.kind == "basic":
            raw = f"{creds.username}:{creds.password}".encode()
            return {"Authorization": "Basic " + base64.b64encode(raw).decode()}
        if creds.access_token:
            return {"Authorization": f"{creds.token_type} {creds.access_token}"}
        raise AuthError(f"{self.display_name}: no usable credentials, reconnect required")

    async def _on_auth_failure(self) -> None:
        assert isinstance(self.auth_client, OAuthClient)
        await self.auth_client.force_refresh(self.integration_data)

    @property
    def api_base(self) -> str:
        base = self.integration_data.credentials.extra.get("api_base")
        if not base:
            raise ConfigError(f"{self.display_name}: stored credentials have no api_base, reconnect required")
        return base.rstrip("/")

    async def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        url = path if path.startswith("http") else self.api_base + path
        return await self.requests_controller.make_request(method, url, **kwargs)

    def parse_json(self, resp: httpx.Response, *, what: str = "response") -> Any:
        """Decode a JSON body or raise a structured UpstreamError. Integrations extend this
        when the vendor signals errors inside a 200 body (see Zoho's parse_body)."""
        try:
            return resp.json()
        except ValueError as exc:
            raise UpstreamError(f"{self.display_name}: {what} returned non-JSON", status=resp.status_code) from exc


    async def handle_task(self, task_name: str, inputs: dict[str, Any]) -> dict[str, Any]:
        try:
            handler = load_task_handler(self.route_name, task_name)
            try:
                parsed = handler.Input(**(inputs or {}))
            except PydanticValidationError as exc:
                detail = "; ".join(
                    f"{'.'.join(str(p) for p in e.get('loc', ())) or 'input'}: {e.get('msg', 'invalid')}" for e in exc.errors()
                ) or "invalid input"
                raise ValidationError(f"invalid inputs for {task_name}: {detail}") from exc
            result: TaskResult = await handler.run(self, parsed)
        except ConnectorError as exc:
            log.info("%s.%s failed: %s", self.route_name, task_name, exc.message)
            result = TaskResult.failure(exc)
        except Exception as exc:  # never leak a traceback to the agent
            log.exception("%s.%s crashed", self.route_name, task_name)
            result = TaskResult.failure(exc)
        headers = self.requests_controller.last_rate_limit_headers()
        if headers:
            result.rate_limit = headers
        return result.to_dict()

    async def check_integration_health(self) -> tuple[bool, str]:
        try:
            _, name = await self.get_and_set_account_name()
            return True, name
        except ConnectorError as exc:
            return False, exc.message

    async def get_and_set_account_name(self, use_existing_name: bool = True) -> tuple[str, str]:
        raise NotImplementedError

    def get_rate_limits_info(self) -> RateLimitsInfo:
        return self.info.rate_limits

    def get_integration_data(self) -> IntegrationObject:
        return self.integration_data

    def get_dynamic_loading(self) -> bool:
        return self.info.dynamic_loading

    async def aclose(self) -> None:
        await self.requests_controller.aclose()
