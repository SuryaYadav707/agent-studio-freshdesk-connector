"""Dump the MCP tool specification (what `tools/list` returns) to mcp_tools.json.

Used for the submission artefact, the docs, and as the tool definitions for the
chat tool loop in the demo UI.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from api.integrations.routers.integrations_handlers import IntegrationsHandlers
from api.integrations.routers.mcp_server import SERVER_INSTRUCTIONS, SERVER_NAME, build_server

SPEC_PATH = Path(__file__).resolve().parents[3] / "mcp_tools.json"


async def collect_spec(handlers: IntegrationsHandlers | None = None) -> dict[str, Any]:
    server = build_server(handlers)
    tools = await server.list_tools()
    return {
        "server": {"name": SERVER_NAME, "instructions": SERVER_INSTRUCTIONS, "transport": ["stdio", "streamable-http"]},
        "tools": [
            {
                "name": t.name,
                "title": t.title,
                "description": t.description,
                "inputSchema": t.input_schema,
                "annotations": t.annotations.model_dump(exclude_none=True, by_alias=True) if t.annotations else None,
                "meta": t.meta,
            }
            for t in tools
        ],
    }


def spec_to_claude_tools(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Anthropic Messages API tool definitions from the MCP spec."""
    return [{"name": t["name"], "description": t["description"], "input_schema": t["inputSchema"]} for t in spec["tools"]]


def write_spec(path: Path = SPEC_PATH, handlers: IntegrationsHandlers | None = None) -> Path:
    spec = asyncio.run(collect_spec(handlers))
    path.write_text(json.dumps(spec, indent=2) + "\n")
    return path
