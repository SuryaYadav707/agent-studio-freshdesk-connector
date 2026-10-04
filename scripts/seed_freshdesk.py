"""Create fictional support tickets in the connected Freshdesk trial helpdesk.

    connector seed freshdesk

Uses POST /api/v2/tickets through the same RequestsController as the read
tools (so the trial's 50 calls/min limit is respected). All names, emails and
problems are invented; emails use the reserved example.test domain.
"""
from __future__ import annotations

import asyncio
import sys

from api.integrations.models.errors import ConnectorError
from api.integrations.routers.integrations_handlers import IntegrationsHandlers

# status: 2 open, 3 pending, 4 resolved, 5 closed | priority: 1 low, 2 medium, 3 high, 4 urgent
TICKETS = [
    dict(subject="Refund not received for order ORD-10231", email="asha.demo@example.test", status=2, priority=4, type="Incident",
         tags=["billing", "refund"], description="I returned the steel bottle 12 days ago and the refund has not reached my card."),
    dict(subject="Autopay failed twice this month", email="ravi.demo@example.test", status=2, priority=3, type="Problem",
         tags=["payments", "autopay"], description="My subscription autopay failed on the 1st and the 3rd. Card has balance."),
    dict(subject="Cannot download GST invoice", email="meera.demo@example.test", status=3, priority=2, type="Question",
         tags=["invoice"], description="The invoice download button spins forever on the orders page."),
    dict(subject="Wrong size delivered", email="karan.demo@example.test", status=2, priority=3, type="Incident",
         tags=["shipping", "exchange"], description="Ordered XL, received M. Need an exchange before the 15th."),
    dict(subject="Promo code FEST20 rejected at checkout", email="nina.demo@example.test", status=2, priority=2, type="Question",
         tags=["promo"], description="Code shows as expired but the email says valid till month end."),
    dict(subject="Duplicate charge on UPI", email="dev.demo@example.test", status=2, priority=4, type="Incident",
         tags=["payments", "duplicate"], description="Charged twice for one order. Both UPI references attached."),
    dict(subject="Change delivery address", email="sana.demo@example.test", status=4, priority=1, type="Request",
         tags=["shipping"], description="Please update the address on my open order to the office address."),
    dict(subject="App crashes on login (Android 14)", email="arjun.demo@example.test", status=2, priority=3, type="Problem",
         tags=["app", "android"], description="Crash right after OTP entry since the latest update."),
    dict(subject="Loyalty points missing after purchase", email="priya.demo@example.test", status=3, priority=2, type="Question",
         tags=["loyalty"], description="Bought on the 2nd, points not credited after 72 hours."),
    dict(subject="Bulk order quote for 200 bottles", email="procurement.demo@example.test", status=2, priority=2, type="Request",
         tags=["b2b", "quote"], description="Need a quote and lead time for 200 units of the 1L steel bottle."),
    dict(subject="Subscription cancelled but still billed", email="zoya.demo@example.test", status=2, priority=4, type="Incident",
         tags=["billing", "subscription"], description="Cancelled last month, billed again today."),
    dict(subject="Thank you, issue resolved", email="omar.demo@example.test", status=5, priority=1, type="Question",
         tags=["feedback"], description="The replacement arrived. Closing this one."),
]


async def main() -> int:
    handlers = IntegrationsHandlers()
    try:
        integration = await handlers.get_integration("freshdesk")
    except ConnectorError as exc:
        print(f"error: {exc.message}", file=sys.stderr)
        return 1

    created = 0
    for ticket in TICKETS:
        try:
            resp = await integration.request("POST", "/tickets", json=ticket)
            tid = resp.json().get("id")
            if tid is None:
                print(f"failed: {ticket['subject']}: no id in response", file=sys.stderr)
                continue
            created += 1
            print(f"created ticket #{tid}: {ticket['subject']}")
        except ConnectorError as exc:
            print(f"failed: {ticket['subject']}: {exc.message}", file=sys.stderr)
    print(f"done: {created}/{len(TICKETS)} tickets created")
    return 0 if created else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
