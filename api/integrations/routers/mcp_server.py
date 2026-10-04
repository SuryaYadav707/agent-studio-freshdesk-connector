"""MCP server: one tool per entry in INTEGRATION_FUNCTIONS.

Tool name:        {route}_{task}            e.g. zoho_inventory_list_items
Tool description: the handler's HELPER_TEXT
Tool inputSchema: derived from the handler's pydantic Input model
Tool result:      the TaskResult dict ({ok, items|data, page, has_more, error, rate_limit})

The same handler modules power the UI playground and the chat tool loop, so
the spec can never drift from the implementation.
"""
from __future__ import annotations

import inspect
from typing import Annotated, Any, Callable

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from api.integrations.constants.constants import IntegrationsRouteNameMapping
from api.integrations.lib.integrations.integration_base import load_task_handler
from api.integrations.routers.integrations_handlers import IntegrationsHandlers

SERVER_NAME = "agent-studio-connector"
SERVER_INSTRUCTIONS = (
    "Read-only connector for merchant tools. Freshdesk tools read support tickets (API key auth). "
    "Zoho Inventory tools read items, stock and sales orders (OAuth 2.0). Every result is JSON with "
    "`ok`; on failure `error.type` is one of auth_error, config_error, not_found, rate_limited, "
    "upstream_error, validation_error. When rate_limited, wait `error.retry_after` seconds. "
    "Paginate with `page` while `has_more` is true."
)


def tool_name(route: str, task: str) -> str:
    return f"{route}_{task}"


def _signature_from_model(model) -> tuple[inspect.Signature, dict[str, Any]]:
    """Turn a pydantic Input model into keyword-only parameters with descriptions kept."""
    params: list[inspect.Parameter] = []
    annotations: dict[str, Any] = {}
    for name, field in model.model_fields.items():
        annotation = field.annotation
        if field.description:
            annotation = Annotated[annotation, Field(description=field.description)]
        if field.is_required():
            default = inspect.Parameter.empty
        elif field.default_factory is not None:
            default = field.default_factory()
        else:
            default = field.default
        params.append(inspect.Parameter(name, inspect.Parameter.KEYWORD_ONLY, default=default, annotation=annotation))
        annotations[name] = annotation
    annotations["return"] = dict[str, Any]
    return inspect.Signature(params, return_annotation=dict[str, Any]), annotations


def make_tool(handlers: IntegrationsHandlers, route: str, task: str) -> Callable[..., Any]:
    handler = load_task_handler(route, task)
    signature, annotations = _signature_from_model(handler.Input)

    async def tool(**kwargs: Any) -> dict[str, Any]:
        return await handlers.handle_task(route, task, kwargs)

    tool.__name__ = tool_name(route, task)
    tool.__qualname__ = tool.__name__
    tool.__doc__ = handler.HELPER_TEXT
    tool.__signature__ = signature  # type: ignore[attr-defined]
    tool.__annotations__ = annotations
    return tool


def build_server(handlers: IntegrationsHandlers | None = None) -> MCPServer:
    handlers = handlers or IntegrationsHandlers()
    server = MCPServer(SERVER_NAME, instructions=SERVER_INSTRUCTIONS, version="0.1.0")
    for route, task in handlers.tasks():
        handler = load_task_handler(route, task)
        server.add_tool(
            make_tool(handlers, route, task),
            name=tool_name(route, task),
            title=f"{IntegrationsRouteNameMapping[route]}: {handler.TITLE}",
            description=handler.HELPER_TEXT,
            annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True),
            meta={"documentation_url": handler.DOCUMENTATION_URL, "integration": route, "task": task},
            structured_output=False,
        )
    return server
