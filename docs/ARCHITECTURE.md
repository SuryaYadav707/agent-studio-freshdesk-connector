# Architecture

The layout follows a production integrations service (auth routing by type, a shared requests controller, per-task handlers, versioned JSON config), scaled down to two integrations and a demo UI.

```
api/integrations/
├── constants/constants.py          route names, display names, form-auth list, INTEGRATION_FUNCTIONS task registry
├── config/
│   ├── integrations_config.json    per integration: auth type, endpoints, scopes, rate limits, env placeholders
│   ├── integrations_config.py      typed loader, ${ENV_VAR} interpolation, version resolution
│   ├── form_auth_config.py         form fields for API-key integrations
│   └── settings.py                 process settings from .env
├── models/
│   ├── data_models.py              Credentials, IntegrationObject, RateLimitsInfo, TaskResult
│   ├── interfaces.py               Integration, IntegrationAuthClient, RequestsController protocols
│   └── errors.py                   ConnectorError hierarchy with machine-readable `kind`
├── lib/integrations_auth/
│   ├── auth_client.py              get_auth_client(): form vs OAuth routing
│   ├── form_auth_client.py         API-key path, validates with one live call
│   ├── oauth_client.py             generic OAuth 2.0 code + refresh + revoke, CSRF state, {dc} templating
│   ├── credentials_store.py        JSON per integration, mode 600, flock
│   └── oauth_callback_server.py    one-shot localhost listener for the CLI
├── lib/integrations/
│   ├── integration_base.py         BaseIntegration: controller wiring, task dispatch, registry
│   ├── requests_controller.py      throttle, retry, backoff, auth refresh, error mapping
│   ├── integrations_utils.py       factory over the registry
│   ├── task_utils.py               build_params, Text100 validator
│   ├── freshdesk/                  FreshdeskIntegration + 4 task handlers + models
│   └── zoho_inventory/             ZohoInventoryIntegration + 7 task handlers + models + data-center map
└── routers/
    ├── integrations_handlers.py    shared by MCP server, UI, CLI: auth steps, task runs, health, disconnect
    ├── mcp_server.py               one MCP tool per task, schema from the handler's Input model
    ├── mcp_spec.py                 dumps tools/list to mcp_tools.json, converts to Claude tool defs
    └── __main__.py                 stdio or Streamable HTTP entry point
ui/                                 FastAPI demo: connections, tools playground, streaming agent chat
cli.py                              connector auth | verify | seed | spec | ui | mcp
```

## Request path

```
agent ──MCP──▶ mcp_server tool ──▶ IntegrationsHandlers.handle_task
                                        │ loads IntegrationObject from credentials store
                                        ▼
                               BaseIntegration.handle_task ──▶ <route>_<task>_task_handler.run
                                        │                              │ validates Input, builds params
                                        ▼                              ▼
                               RequestsController.make_request ──▶ vendor API
                                   auth header (refresh if expired) · sliding window · concurrency
                                   429: Retry-After / cooldown / backoff · 401: one re-auth · 5xx retry
                                        │
                                        ▼
                               TaskResult {ok, items|data, page, has_more, error, rate_limit}
```

## Auth routing

`get_auth_client(type)` returns `CommonFormAuthClient` for integrations in `IntegrationsWithFormAuthOnly` (Freshdesk) and `OAuthClient` for `auth_type: oauth2` (Zoho). Both implement the same protocol: `get_form_endpoint`, `get_authorization_url`, `handle_auth_callback`, `refresh_and_fetch_credentials`. Integrations never know which one they have.

Zoho specifics stay in the Zoho module: the `{dc}` placeholder in config URLs, `prepare_callback_data` mapping Zoho's `location` parameter to a data-center suffix, `response_hook` classifying body codes 44 / 45 / 1070, organization discovery after consent.

Scopes: `scope` holds the read-only defaults requested at connect. `action_scope_map` lists extra scopes per action; the seed script is the only action with write scopes and requires its own consent. `sensitive_scopes` documents those with warnings.

## Config-driven tools

A task handler module exposes `TITLE`, `HELPER_TEXT`, `DOCUMENTATION_URL`, a pydantic `Input` model and `async run()`. `mcp_server.py` turns each into an MCP tool: name `{route}_{task}`, description from `HELPER_TEXT`, `inputSchema` from the model, `meta.documentation_url`. `mcp_spec.py` dumps the same list to `mcp_tools.json`, and the chat loop converts it to Claude tool definitions. One source of truth, so the spec cannot drift from the implementation. `INTEGRATION_FUNCTIONS` must list exactly the handler files on disk; `load_task_handler` fails fast otherwise.

## Rate limiting

`RateLimitsInfo` per integration (from JSON): calls per window, window seconds, concurrency, cooldown, backoff strategy, codes, max retries. `RequestsController` applies a sliding window and a semaphore before each call, honours `Retry-After` (seconds or HTTP-date) or falls back to cooldown then exponential backoff with jitter, pauses when the upstream reports zero remaining, retries 5xx and network errors, forces one token refresh on 401, and maps everything else to `ConnectorError` subclasses with secrets redacted. Rate-limit headers from the last response ride along in every `TaskResult`.

## Adding an integration

1. Entry in `integrations_config.json` (auth type, endpoints, scopes, rate limits, `${ENV}` placeholders).
2. Route and display name in `constants.py`, plus `tasks.<route>.<task>` entries.
3. A `BaseIntegration` subclass with `route_name` set. It self-registers. Override `prepare_callback_data` or `response_hook` only if the vendor needs it.
4. One `<route>_<task>_task_handler.py` per tool.
5. Add the module path to `INTEGRATION_MODULES` in `integrations_utils.py`.

No change to the MCP server, spec, UI or CLI.

## Demo UI and chat

The UI calls the same `IntegrationsHandlers` the MCP server uses. The chat page runs a streaming Messages API tool loop: tool definitions from `mcp_spec`, calls dispatched in-process, events streamed over SSE (text deltas, tool start, tool input, tool result, notice, done). The full transcript (assistant blocks, tool results) is replayed on the next turn so the model sees its own earlier calls. Prompt caching on the system prompt, eager tool-input streaming, server-side refusal fallback with the `fallback` block stripped on replay, bounded retry on unparseable tool JSON. Rendering goes through DOMPurify because model output can echo untrusted ticket text.

## Deliberately left out

Multi-tenant storage, external session store, webhooks or listeners, knowledge-base sync, dynamic dropdown metadata, write tools. See LIMITATIONS.md.
