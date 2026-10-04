# Assumptions and limitations

## Assumptions

- **No Agent Studio access.** Candidates cannot register connectors inside Agent Studio (early access, merchant accounts). The connector is an MCP server, which is what Agent Studio's tooling speaks. MCP Inspector and the bundled chat UI act as stand-ins.
- **Read-only is the right default.** The assignment asks for an agent that reads tickets, orders or inventory. No write tool exists, and the Zoho token carries read scopes only. Write support is a config and handler addition (see ARCHITECTURE.md).
- **Single merchant, single process.** Credentials live in `tokens/` on the connector host. One organization per Zoho connection, one helpdesk per Freshdesk connection.
- **Fictional data only.** Seed scripts create invented items, customers, orders and tickets with `example.test` addresses. No real customer data was used or committed.

## Limitations

### Vendor side

- **Freshdesk has no OAuth** for its REST API (vendor statement, March 2025). API key is the only option, and the key inherits the agent's full Freshdesk permissions. Scope restriction happens in the connector, not at the vendor.
- **Freshdesk trial requires a business email** and expires after 21 days. The forever-free plan has no API access.
- **Freshdesk filter API**: 512-character queries, 30 results per page, 10 pages maximum. `include=conversations` returns at most 10 conversations.
- **Zoho free plan**: 1000 API calls per day, 100 per minute, 5 concurrent. Daily exhaustion returns code 45, surfaced as `rate_limited` without retry.
- **Zoho data centers**: the account's region fixes the auth and API hosts. `ZOHO_DC` must match, the callback `location` parameter is used to confirm.
- **Zoho revoke** is best effort. If the revoke endpoint is unreachable, the local credentials are still deleted.

### Connector side

- **Freshdesk not live-verified.** The Freshdesk handlers follow the official reference's parameters and response shapes, but no call was made to a real helpdesk during this submission.
- **Pagination is manual.** Tools return `has_more` and `page`; the agent decides whether to fetch more. No automatic fan-out across pages.
- **No caching.** Every tool call hits the vendor. Fine at demo scale, wasteful for repeated identical questions.
- **File-based credentials.** `tokens/*.json` with mode 600 and an advisory lock. Works for the UI and MCP server as two processes on one host. Not suitable for multiple hosts or tenants; swap `FileCredentialsStore` for a database or secret manager.
- **Session cookie flash.** The UI's one-shot messages ride in a signed cookie. `UI_SECRET` keeps them valid across restarts; unset, each process gets a random key.
- **POSIX only for locking.** `fcntl` is used when present; on Windows the store falls back to no locking.
- **Chat history replays the full transcript** (assistant blocks and tool results) for faithfulness. Capped at 600 KB and 80 messages per session. Long sessions should start a new chat.
- **Cost estimate is approximate.** Token prices are a static table in `ui/agent_loop.py`.



## How this was verified

| Check | Result |
|---|---|
| `ruff check .` | clean |
| Zoho consent flow via UI | consent on `accounts.zoho.in`, callback with `location=in`, token exchange, org bound |
| Zoho seed with write-scope consent, then reconnect read-only | 8 items, 4 customers, 6 draft orders; token back to three read scopes |
| All 7 Zoho tools over real MCP stdio | `verify`, `search_items(low_stock_only)` returned the 4 expected SKUs, `get_sales_order` returned line items, bad input returned `validation_error` |
| Chat, multi-turn | parallel `get_item` calls, prompt-cache hits, transcript replay, safety-fallback notice handled |
| Rate-limit path | 429 handling with `Retry-After` (seconds and HTTP-date), cooldown, backoff exhaustion, Zoho codes 44 / 45, zero-remaining pause; live Zoho headers `x-rate-limit-remaining` echoed in every result |
| OAuth refresh | live: expired token refreshed before a request, 401 forces one refresh and retry; refresh lock shares one refresh across concurrent calls |
| Freshdesk | not live-verified, see above |

## Long-term fix for the Freshdesk gap

Freshworks ships an official hosted MCP server for Freshdesk (GA September 2026, API-key auth, metered to 100 to 1000 actions per month depending on plan). A merchant on Growth or above could use that instead. This private connector remains useful where the monthly action cap is too low for support volume, where read-only guarantees are required, or for tools such as Zoho Inventory that have no official MCP server.
