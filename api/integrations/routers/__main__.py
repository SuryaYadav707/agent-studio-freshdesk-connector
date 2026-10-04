"""Run the MCP server.

    python -m api.integrations.routers                       # stdio (Claude Code, Claude Desktop, Inspector)
    python -m api.integrations.routers --transport http      # Streamable HTTP on http://127.0.0.1:8000/mcp
"""
from __future__ import annotations

import argparse
import logging
import sys

from api.integrations.models.errors import ConnectorError
from api.integrations.routers.mcp_server import build_server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="connector-mcp", description="Agent Studio connector MCP server")
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--log-level", default="WARNING")
    args = parser.parse_args(argv)

    # stdio transport uses stdout for protocol frames: logs must go to stderr.
    logging.basicConfig(level=args.log_level, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")

    try:
        server = build_server()
    except ConnectorError as exc:
        print(f"error: {exc.message}", file=sys.stderr)
        return 2

    if args.transport == "http":
        server.run(transport="streamable-http", host=args.host, port=args.port)
    else:
        server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
