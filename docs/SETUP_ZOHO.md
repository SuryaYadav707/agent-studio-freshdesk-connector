# Zoho Inventory setup

About 15 minutes. No company registration, no card. A personal Gmail address works.

## 1. Zoho account and Inventory organization

1. Open https://www.zoho.com/inventory/signup/ and sign up.
2. Note which site you land on after login. `zoho.in` means data center `in`, `zoho.com` means `com`, `zoho.eu` means `eu`. An Indian phone number usually lands on `.in`.
3. Zoho Inventory asks for organization details. Use a fictional name such as "Demo Store", your country, currency and time zone. Inventory start date: pick a date at least two weeks in the past so seeded orders fit.
4. Choose the **Free** plan if prompted. Skip the setup wizard.

## 2. OAuth client in the Zoho API console

1. Open the API console for your data center: https://api-console.zoho.in/ (or `.com`, `.eu`). Log in with the same account.
2. **Add Client** and choose **Server-based Applications**. The connector runs on a backend, holds the client secret and uses a redirect URI plus refresh token, which is what this type is for. Self Client has no consent screen, Client-based has no refresh token.
3. Fill in:
   - Client name: `Agent Studio connector`
   - Homepage URL: `http://localhost:8765`
   - Authorized Redirect URIs: `http://localhost:8765/oauth2callback/zoho_inventory`
4. Create, then copy **Client ID** and **Client Secret**.

## 3. `.env`

```
CONNECTOR_INTEGRATIONS=zoho_inventory          # add ,freshdesk when that is set up too
ZOHO_CLIENT_ID=<client id>
ZOHO_CLIENT_SECRET=<client secret>
ZOHO_DC=in                                     # must match the account's data center
UI_BASE_URL=http://localhost:8765              # must match the registered redirect URI
```

## 4. Connect

Either:

```bash
connector ui       # open http://localhost:8765, Zoho card, "Connect with Zoho"
```

or

```bash
connector auth zoho_inventory
```

Zoho shows the consent screen with three read permissions (settings, items, sales orders). Accept. The connector exchanges the code, reads `location` from the callback to confirm the data center, calls `GET /organizations`, and binds the organization. If the account has several organizations and none is default, set `ZOHO_ORGANIZATION_ID` and reconnect.

Verify:

```bash
connector verify zoho_inventory
# [ok] zoho_inventory: Demo Store (org 6009xxxxxxx, zoho.in)
```

## 5. Seed demo data (optional, one-time write consent)

The runtime token is read-only, so seeding needs a separate consent:

1. Open http://localhost:8765/authorize/zoho_inventory?actions=seed and accept. The screen now also lists add / update / delete items, add contacts, add / delete sales orders.
2. `connector seed zoho_inventory` creates 8 items, 4 customers and 6 draft sales orders. It is idempotent.
3. Disconnect and connect again (plain "Connect with Zoho") so the stored token goes back to the three read scopes. The card shows the scopes.

Zoho detail worth knowing: an item must have `can_be_sold=true` and a `sales_rate` to appear on a sales order. `item_type=inventory` alone creates a tracked-only item that Zoho rejects on orders (error 36073). The seed script sets both.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `invalid_client` on consent | Client id / secret wrong, or `ZOHO_DC` does not match the account's data center |
| `redirect_uri_mismatch` | Registered URI differs from `UI_BASE_URL` + `/oauth2callback/zoho_inventory` |
| `OAuth state mismatch` | Consent took longer than 10 minutes, or the UI was restarted between authorize and callback. Click Connect again |
| `config_error: multiple organizations` | Set `ZOHO_ORGANIZATION_ID` in `.env`, reconnect |
| HTTP 429 with code 45 | Daily quota (1000 calls on the free plan) exhausted. Resets at midnight PST |
