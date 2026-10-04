# Agent Studio connector: Zoho Inventory + Freshdesk

Read-only connector that lets an Agent Studio style agent read **inventory and sales orders from Zoho Inventory** and **support tickets from Freshdesk**. Exposed as an MCP server (stdio or Streamable HTTP), with a demo web UI for connecting accounts, trying tools, and chatting with an agent that uses them.

Built for the Razorpay Forward-Deployed Engineer assignment, option 3. Two tools on one shared core, to show both auth patterns the assignment names:

| Integration | Auth | Primitives | Live-verified |
|---|---|---|---|
| Zoho Inventory | OAuth 2.0 authorization code + refresh, per data center | organizations, items (list / get / search), sales orders (list / get) | Yes, free-plan org on `zoho.in` |
| Freshdesk | API key (HTTP Basic), validated on connect | tickets (list / get / search) | No. Trial signup requires a business email, see [LIMITATIONS](docs/LIMITATIONS.md) |

Everything the agent can call is read-only. The only write path is the seed script, which needs a separate consent with write scopes and never touches the agent's token.

## Contents

| Path | What |
|---|---|
| `api/integrations/` | Connector core: auth routing by type, `RequestsController` with rate limiting, per-task handlers, versioned JSON config. [ARCHITECTURE](docs/ARCHITECTURE.md) |
| `api/integrations/routers/` | MCP server, spec generator, shared request handlers |
| `mcp_tools.json` | Generated MCP tool specification (11 tools) |
| `ui/` | Demo UI: Connections, Tools playground, Agent chat |
| `scripts/` | Seed fictional data into the connected accounts |
| `docs/AGENT_CAPABILITIES.md` | What the agent can and cannot do |
| `docs/SETUP_ZOHO.md`, `docs/SETUP_FRESHDESK.md` | Account and credential setup |
| `docs/LIMITATIONS.md` | Assumptions, limits, what was verified and how |

## Setup

Requirements: Python 3.10+. Node 18+ only if you want MCP Inspector.

```bash
git clone <repo> && cd agent-studio-connector
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env          # fill in the values below
```

Accounts (no company needed, personal email works for Zoho):

- Zoho Inventory free plan and an OAuth client in the Zoho API console: [docs/SETUP_ZOHO.md](docs/SETUP_ZOHO.md)
- Freshdesk 21-day trial and its API key: [docs/SETUP_FRESHDESK.md](docs/SETUP_FRESHDESK.md)

`.env` keys that matter: `ZOHO_CLIENT_ID`, `ZOHO_CLIENT_SECRET`, `ZOHO_DC`, `FRESHDESK_DOMAIN`, `FRESHDESK_API_KEY`, `ANTHROPIC_API_KEY` (chat page only), `CONNECTOR_INTEGRATIONS` to enable one or both. Never commit `.env` or `tokens/`.

## Run

```bash
connector ui                       # http://localhost:8765  connect accounts, try tools, chat
connector auth zoho_inventory      # CLI alternative: opens Zoho consent, captures the callback
connector auth freshdesk           # validates FRESHDESK_* from .env and stores them
connector verify                   # live health check of connected integrations
connector seed zoho_inventory      # fictional items, customers, orders (one consent with ?actions=seed first)
connector seed freshdesk           # 12 fictional tickets
connector spec                     # regenerate mcp_tools.json
connector mcp                      # MCP server over stdio
connector mcp --transport http     # Streamable HTTP on http://127.0.0.1:8000/mcp
```

### Attach to an MCP client

```bash
# MCP Inspector (browser UI for tools/list and tools/call)
npx @modelcontextprotocol/inspector .venv/bin/python -m api.integrations.routers
```

Any MCP client works the same way: start command `python -m api.integrations.routers` over stdio, or point it at `http://127.0.0.1:8000/mcp` when running `connector mcp --transport http`.

## Tools

| Tool | Purpose |
|---|---|
| `zoho_inventory_verify_connection` | Validate / refresh the token, bind the organization |
| `zoho_inventory_list_organizations` | Organizations visible to the account |
| `zoho_inventory_list_items` | Items with stock, reorder level, `is_low_stock`; filter, sort, paginate |
| `zoho_inventory_get_item` | One item with per-location stock |
| `zoho_inventory_search_items` | By text, name / SKU substring, low stock only |
| `zoho_inventory_list_sales_orders` | Status, customer, number, date-range filters |
| `zoho_inventory_get_sales_order` | One order with line items, totals, addresses |
| `freshdesk_verify_connection` | Validate the key, report agent and helpdesk |
| `freshdesk_list_tickets` | Predefined views, requester / company filters, `updated_since`, pagination |
| `freshdesk_get_ticket` | One ticket with description, requester, stats, optional conversations |
| `freshdesk_search_tickets` | Structured filters compiled to Freshdesk's query syntax |

Every tool returns `{ok, items|data, page, has_more, total_count?, rate_limit?}` or `{ok: false, error: {type, message, retry_after?}}` with `type` in `auth_error | config_error | not_found | rate_limited | upstream_error | validation_error`. Errors are data, never exceptions, so the agent can decide what to do. Each tool carries `meta.documentation_url` pointing at the vendor endpoint it wraps.

## Rate limiting

One `RequestsController` per integration, configured by the `rate_limits` block in `integrations_config.json`:

- client-side sliding window (Zoho 100/min, Freshdesk trial 50/min) plus a concurrency cap (Zoho allows 5)
- on 429: honour `Retry-After` (seconds or HTTP-date), else the configured cooldown, then exponential backoff with jitter, bounded retries, then a structured `rate_limited` error carrying `retry_after`
- Zoho body codes: 44 (per minute) retries, 45 (daily quota) fails fast, 1070 (concurrency) short retry
- proactive pause when the upstream reports zero remaining calls
- one forced token refresh on 401, bounded retries on 5xx and network errors
- upstream rate-limit headers echoed in every result as `rate_limit`

## Demo UI

- **Connections**: Freshdesk form (key validated with one live call before it is stored), Zoho "Connect" button through the real consent screen, health check, disconnect (revokes the Zoho grant upstream).
- **Tools playground**: every MCP tool, form generated from its `inputSchema`, JSON result with rate-limit headers.
- **Agent chat**: streaming Messages API tool loop over the same tools. Developer view shows each tool call as it happens with arguments, result, latency, rate-limit headroom, tokens and estimated cost. Merchant view shows plain-language activity and the data scope only.

## Verified

- `ruff check .` clean.
- Live against a Zoho Inventory free-plan org (`.in` data center): consent flow, callback with `location`, token exchange, organization discovery, seeding with a separate write-scope consent, reconnect with read-only scopes, all seven Zoho tools over real MCP stdio, multi-turn chat with parallel tool calls and prompt-cache hits.
- Freshdesk: handlers written against the request parameters and response shapes in the official API reference. Not exercised against a live helpdesk.

Full list in [docs/LIMITATIONS.md](docs/LIMITATIONS.md).
