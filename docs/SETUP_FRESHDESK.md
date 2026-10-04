# Freshdesk setup

Freshdesk's REST API supports **API key authentication only**. Freshworks confirmed in March 2025 that OAuth for the Freshdesk API is not on the roadmap. The connector therefore uses the form-based (API key) auth path for Freshdesk.

## 1. Trial helpdesk

1. Go to https://www.freshworks.com/freshdesk/signup/ and start the free trial (21 days, full features, API access at 50 calls per minute).
2. Freshworks rejects gmail, outlook and other public mailbox domains. You need an address on a custom or company domain. Options: a work or university address, or a cheap domain with free email forwarding (Cloudflare Email Routing, ImprovMX).
3. Pick a fictional helpdesk name. It becomes the subdomain: `acme-demo` gives `acme-demo.freshdesk.com`.
4. Confirm the email, skip the onboarding wizard. Do not import real data.

Note: the forever-free Freshdesk plan has **no** API access. The trial is required.

## 2. API key

1. Log in to `https://<subdomain>.freshdesk.com`.
2. Profile picture (top right) > **Profile settings**.
3. Right panel, **Your API key** > **View API key**. Copy it.
4. If the section is missing, Admin > Account > Security, confirm API access is enabled for your role.

## 3. `.env`

```
CONNECTOR_INTEGRATIONS=zoho_inventory,freshdesk
FRESHDESK_DOMAIN=acme-demo            # subdomain only
FRESHDESK_API_KEY=<key>
```

## 4. Connect

Either the Freshdesk card in `connector ui` (domain + key form, validated with one call to `GET /api/v2/agents/me` before it is stored), or:

```bash
connector auth freshdesk
connector verify freshdesk
# [ok] freshdesk: you@example.test @ acme-demo.freshdesk.com
```

## 5. Seed demo tickets (optional)

```bash
connector seed freshdesk     # 12 fictional tickets, example.test requester emails
```

## Permissions

An API key inherits everything its agent can do in Freshdesk, there are no scopes. The connector only exposes read tools, so the agent cannot write, but the key itself could. Use a dedicated agent with the lowest role Freshdesk allows for API access.

## Status in this submission

Freshdesk was **not** exercised against a live helpdesk because the trial signup rejected the available email addresses. The handlers follow the request parameters and response shapes from the official reference (https://developers.freshdesk.com/api/). See [LIMITATIONS.md](LIMITATIONS.md).
