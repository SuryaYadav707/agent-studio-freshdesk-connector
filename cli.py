"""`connector` command line.

    connector auth freshdesk            validate FRESHDESK_DOMAIN / FRESHDESK_API_KEY from .env and store them
    connector auth zoho_inventory       open the Zoho consent screen, capture the callback, store tokens
    connector verify [integration]      live health check of connected integrations
    connector spec                      write mcp_tools.json
    connector seed <integration>        create fictional demo data
    connector ui                        run the demo web UI (http://localhost:8765)
    connector mcp [--transport http]    run the MCP server
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import webbrowser
from urllib.parse import urlparse

from dotenv import load_dotenv


def _handlers():
    from api.integrations.routers.integrations_handlers import IntegrationsHandlers

    return IntegrationsHandlers()


def cmd_auth(args: argparse.Namespace) -> int:
    from api.integrations.config.form_auth_config import get_form_fields
    from api.integrations.config.integrations_config import get_integration_info
    from api.integrations.config.settings import get_settings
    from api.integrations.constants.constants import IntegrationsRouteNameMapping
    from api.integrations.lib.integrations_auth.oauth_callback_server import wait_for_callback

    handlers = _handlers()
    route = args.integration
    kind = handlers.auth_kind(route)

    if kind == "form":
        data = {}
        for spec in get_form_fields(IntegrationsRouteNameMapping[route]):
            value = os.environ.get(spec["env"], "")
            if not value:
                print(f"missing {spec['env']} in environment / .env", file=sys.stderr)
                return 2
            data[spec["field"]] = value
        obj = asyncio.run(handlers.handle_callback(route, data))
        print(f"connected {route}: {obj.account_name}")
        return 0

    url, _state = handlers.get_authorization_url(route, actions=args.actions or None, dc=args.dc)
    info = get_integration_info(IntegrationsRouteNameMapping[route])
    settings = get_settings()
    port = settings.ui_port
    print(f"opening browser for consent (listening on {settings.ui_base_url}{info.redirect_endpoint}) ...")
    print(url)
    if not args.no_browser:
        webbrowser.open(url)
    query = wait_for_callback(urlparse(info.redirect_endpoint).path, port)
    obj = asyncio.run(handlers.handle_callback(route, query))
    print(f"connected {route}: {obj.account_name}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    handlers = _handlers()
    routes = [args.integration] if args.integration else handlers.enabled
    rc = 0
    for route in routes:
        result = asyncio.run(handlers.health(route))
        status = "ok" if result["connected"] else "FAIL"
        print(f"[{status}] {route}: {result.get('account_name') or result.get('message') or 'not connected'}")
        if result.get("rate_limit"):
            print("       rate limit headers:", json.dumps(result["rate_limit"]))
        if not result["connected"]:
            rc = 1
    return rc


def cmd_spec(_args: argparse.Namespace) -> int:
    from api.integrations.routers.mcp_spec import write_spec

    path = write_spec()
    spec = json.loads(path.read_text())
    print(f"wrote {path} with {len(spec['tools'])} tools")
    for tool in spec["tools"]:
        print("  -", tool["name"])
    return 0


def cmd_seed(args: argparse.Namespace) -> int:
    # scripts/ is not an installed package; make it importable from the repo root
    root = os.path.dirname(os.path.abspath(__file__))
    if root not in sys.path:
        sys.path.insert(0, root)
    if args.integration == "freshdesk":
        from scripts.seed_freshdesk import main as seed

        return asyncio.run(seed())
    if args.integration == "zoho_inventory":
        from scripts.seed_zoho_inventory import main as seed

        return asyncio.run(seed())
    print("unknown integration", file=sys.stderr)
    return 2


def cmd_ui(args: argparse.Namespace) -> int:
    import uvicorn

    from api.integrations.config.settings import get_settings

    settings = get_settings()
    uvicorn.run("ui.app:app", host="127.0.0.1", port=settings.ui_port, reload=args.reload, log_level="info")
    return 0


def cmd_mcp(args: argparse.Namespace) -> int:
    from api.integrations.routers.__main__ import main as mcp_main

    argv = ["--transport", args.transport, "--port", str(args.port)]
    return mcp_main(argv)


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="connector", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("auth", help="connect an integration")
    p.add_argument("integration", choices=["freshdesk", "zoho_inventory"])
    p.add_argument("--dc", help="Zoho data center override (com, in, eu, ...)")
    p.add_argument("--actions", nargs="*", help="extra action_scope_map keys to request (e.g. seed)")
    p.add_argument("--no-browser", action="store_true")
    p.set_defaults(func=cmd_auth)

    p = sub.add_parser("verify", help="live health check")
    p.add_argument("integration", nargs="?", choices=["freshdesk", "zoho_inventory"])
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("spec", help="write mcp_tools.json")
    p.set_defaults(func=cmd_spec)

    p = sub.add_parser("seed", help="create fictional demo data")
    p.add_argument("integration", choices=["freshdesk", "zoho_inventory"])
    p.set_defaults(func=cmd_seed)

    p = sub.add_parser("ui", help="run the demo UI")
    p.add_argument("--reload", action="store_true")
    p.set_defaults(func=cmd_ui)

    p = sub.add_parser("mcp", help="run the MCP server")
    p.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=cmd_mcp)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # surfaced as one line, no traceback for the demo
        from api.integrations.models.errors import ConnectorError

        if isinstance(exc, ConnectorError):
            print(f"error: {exc.message}", file=sys.stderr)
            return 1
        raise


if __name__ == "__main__":
    sys.exit(main())
