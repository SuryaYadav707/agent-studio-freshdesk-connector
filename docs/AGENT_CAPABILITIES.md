# What the agent can and cannot do

Scope of an Agent Studio agent that has this connector attached.

## In one line

The agent can **read** inventory, sales orders and support tickets and reason over them. It **cannot change anything** in Zoho Inventory or Freshdesk, and it cannot contact anyone.

## Can

### Zoho Inventory (OAuth 2.0, scopes `settings.READ`, `items.READ`, `salesorders.READ`)

- Confirm the connection is healthy and which organization it is bound to (`zoho_inventory_verify_connection`).
- List the organizations the Zoho account can see (`zoho_inventory_list_organizations`).
- List items with SKU, status, unit, sales and purchase rate, stock on hand, available stock, reorder level and a computed `is_low_stock` flag. Filter by status or item type (for example `Status.Lowstock`), sort by name, SKU, rate or stock, paginate (`zoho_inventory_list_items`).
- Fetch one item with description, brand, and stock per location or warehouse (`zoho_inventory_get_item`).
- Find items by free text or by name / SKU substring, optionally low stock only (`zoho_inventory_search_items`).
- List sales orders with status, customer, total, currency, shipment date and invoiced / paid / shipped flags. Filter by status, customer name or id, order number, free text or a date range (`zoho_inventory_list_sales_orders`).
- Fetch one sales order with line items (item, SKU, quantities ordered / invoiced / shipped, rate), totals, addresses and notes (`zoho_inventory_get_sales_order`).

Typical questions: "Which items are below reorder level?", "Can the latest draft order be fulfilled from stock?", "Which customer ordered the most this month?", "What is on order SO-00002?"

### Freshdesk (API key)

- Confirm the key works and which agent and helpdesk it belongs to (`freshdesk_verify_connection`).
- List tickets, newest first, using Freshdesk's predefined views, requester id / email, company id, or an `updated_since` cutoff, with pagination (`freshdesk_list_tickets`).
- Fetch one ticket with description, requester, SLA timestamps and optionally the latest replies and notes (`freshdesk_get_ticket`).
- Filter tickets by status, priority, tag, type, assigned agent or group, and created / updated date ranges (`freshdesk_search_tickets`).

Typical questions: "Which urgent tickets are still open?", "Show tickets tagged billing from last week", "What did the customer say on ticket 42?"

### Across both

- Combine sources in one answer, for example "customer X has an open ticket about order Y, is the item in stock?".
- Paginate when a result says `has_more` and the question needs the rest.
- Explain failures in plain words using the structured `error.type`: reconnect needed, rate limited (wait `retry_after` seconds), not found, bad input, vendor outage.

## Cannot

- **Write anything.** No create, update, delete, status change, note, reply, invoice, shipment, stock adjustment. There is no tool for it, and the Zoho token has no write scope. Asking the agent to "mark an item inactive" or "close a ticket" gets a refusal and a suggestion of what the merchant can do themselves.
- **Send anything.** No email, WhatsApp, SMS, calls, webhooks.
- **See purchase orders, bills, invoices, payments, vendors, composite items, or Freshdesk contacts / companies / knowledge base.** Only the primitives listed above are implemented.
- **Act across organizations.** One Zoho organization per connection. Switching requires `ZOHO_ORGANIZATION_ID` or a reconnect.
- **Bypass vendor limits.** Freshdesk filter queries are capped at 512 characters, 30 results per page, 10 pages. Freshdesk returns at most the 10 most recent conversations on a ticket. Zoho's free plan allows 1000 API calls per day. The connector surfaces these as `rate_limited` or `validation_error`, it does not work around them.
- **Guarantee freshness.** Every call is live, nothing is cached, but Zoho stock figures lag a few seconds behind the UI after an adjustment.
- **Guess.** The system prompt forbids inventing ids, quantities or amounts. If a tool fails, the agent says so.

## Trust boundaries

- Credentials never pass through the agent. They live in `tokens/` on the connector host (mode 600) and only the `RequestsController` reads them.
- Tool inputs are validated before any API call. Freshdesk filter syntax is compiled from whitelisted fields, so agent text never reaches the query language raw.
- Ticket bodies, item names and customer names are untrusted content. The demo UI sanitizes every rendered answer.
- Each tool result carries the upstream rate-limit headers so the agent, and the merchant watching, can see the remaining budget.

## Extending

Adding a write tool later is a config and handler change, not a redesign: add `tasks.zoho_inventory.create_sales_order` to the task registry, a handler file, and a scope entry in `action_scope_map`. The consent screen then asks for that scope only when the action is enabled.
