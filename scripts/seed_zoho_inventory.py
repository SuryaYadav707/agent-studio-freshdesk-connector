"""Create fictional items, customers and sales orders in the connected Zoho Inventory org.

Idempotent: existing SKUs are updated instead of duplicated, existing customers
are reused, and orders are only created when the org has none. Leftover
`ZZ-*` test items/orders are removed.

Requires a token with write scopes, which the runtime connection does not have:
    open http://localhost:8765/authorize/zoho_inventory?actions=seed   (or: connector auth zoho_inventory --actions seed)
    connector seed zoho_inventory
    reconnect normally afterwards so the agent token is read-only again

Zoho gotcha: an item must have can_be_sold=true (plus sales_rate) to appear on a
sales order; item_type="inventory" alone yields a "tracked only" item (error 36073).

All data is invented.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import date, timedelta

from api.integrations.config.integrations_config import get_integration_info
from api.integrations.models.errors import ConnectorError
from api.integrations.routers.integrations_handlers import IntegrationsHandlers

ITEMS = [
    dict(name="Steel Bottle 1L", sku="BOT-1L", rate=499, purchase_rate=250, initial_stock=4, reorder_level=10),
    dict(name="Steel Bottle 500ml", sku="BOT-500", rate=349, purchase_rate=180, initial_stock=60, reorder_level=15),
    dict(name="Bamboo Lunch Box", sku="LBX-BAM", rate=899, purchase_rate=450, initial_stock=8, reorder_level=12),
    dict(name="Cotton Tote Bag", sku="TOTE-CT", rate=249, purchase_rate=90, initial_stock=140, reorder_level=30),
    dict(name="Insulated Mug 350ml", sku="MUG-350", rate=599, purchase_rate=300, initial_stock=0, reorder_level=10),
    dict(name="Glass Jar Set (3)", sku="JAR-3", rate=799, purchase_rate=400, initial_stock=22, reorder_level=10),
    dict(name="Copper Water Jug", sku="JUG-CU", rate=1499, purchase_rate=800, initial_stock=5, reorder_level=5),
    dict(name="Yoga Mat 6mm", sku="MAT-6", rate=1299, purchase_rate=650, initial_stock=35, reorder_level=10),
]
CUSTOMERS = ["Ravi Demo Traders", "Asha Demo Retail", "Meera Demo Stores", "Karan Demo Supplies"]
ORDERS = [  # (customer index, [(sku, qty)], days_ago)
    (0, [("BOT-1L", 2), ("TOTE-CT", 10)], 1),
    (1, [("LBX-BAM", 3)], 2),
    (2, [("MAT-6", 5), ("MUG-350", 2)], 4),
    (3, [("JAR-3", 6)], 6),
    (0, [("BOT-500", 20)], 9),
    (1, [("JUG-CU", 1), ("BOT-1L", 1)], 12),
]


def item_payload(item: dict, *, create: bool) -> dict:
    body = dict(
        name=item["name"], sku=item["sku"], unit="pcs", product_type="goods",
        rate=item["rate"], sales_rate=item["rate"], purchase_rate=item["purchase_rate"],
        can_be_sold=True, can_be_purchased=True, reorder_level=item["reorder_level"],
        description=f"Demo product {item['sku']} (fictional)",
    )
    if create:
        body.update(item_type="inventory", track_inventory=True,
                    initial_stock=item["initial_stock"], initial_stock_rate=item["purchase_rate"])
    return body


async def main() -> int:
    handlers = IntegrationsHandlers()
    try:
        integration = await handlers.get_integration("zoho_inventory")
        await integration.get_and_set_account_name()
    except ConnectorError as exc:
        print(f"error: {exc.message}", file=sys.stderr)
        return 1

    info = get_integration_info("Zoho Inventory")
    needed = set(info.action_scope_map.get("seed", [])) - set(info.default_scopes())
    granted = set(integration.integration_data.credentials.scopes)
    if needed and not needed <= granted:
        missing = ", ".join(sorted(needed - granted))
        print(f"token lacks write scopes ({missing}). Open http://localhost:8765/authorize/zoho_inventory?actions=seed, "
              "accept, then rerun.", file=sys.stderr)
        return 2

    resp = await integration.request("GET", "/items", params={"per_page": 200})
    existing = {i["sku"]: i for i in integration.parse_body(resp, "items") or [] if i.get("sku")}
    item_ids: dict[str, str] = {}
    for item in ITEMS:
        try:
            if item["sku"] in existing:
                iid = existing[item["sku"]]["item_id"]
                await integration.request("PUT", f"/items/{iid}", json=item_payload(item, create=False))
                print(f"item {item['sku']} updated")
            else:
                r = await integration.request("POST", "/items", json=item_payload(item, create=True))
                iid = integration.parse_body(r, "item")["item_id"]
                print(f"item {item['sku']} created")
            item_ids[item["sku"]] = iid
        except ConnectorError as exc:
            print(f"item {item['sku']} failed: {exc.message}", file=sys.stderr)

    resp = await integration.request("GET", "/salesorders", params={"per_page": 200})
    orders_existing = integration.parse_body(resp, "salesorders") or []
    for so in orders_existing:
        detail = integration.parse_body(await integration.request("GET", f"/salesorders/{so['salesorder_id']}"), "salesorder")
        if any(str(li.get("sku", "")).startswith("ZZ-") for li in detail.get("line_items", [])):
            try:
                await integration.request("DELETE", f"/salesorders/{so['salesorder_id']}")
                print(f"deleted test order {so['salesorder_number']}")
                orders_existing = [o for o in orders_existing if o["salesorder_id"] != so["salesorder_id"]]
            except ConnectorError as exc:
                print(f"delete order {so['salesorder_number']} failed: {exc.message}", file=sys.stderr)
    for sku, it in existing.items():
        if sku.startswith("ZZ-"):
            try:
                await integration.request("DELETE", f"/items/{it['item_id']}")
                print(f"deleted test item {sku}")
            except ConnectorError as exc:
                print(f"delete item {sku} failed: {exc.message}", file=sys.stderr)

    resp = await integration.request("GET", "/contacts", params={"contact_type": "customer", "per_page": 200})
    contacts = {c["contact_name"]: c["contact_id"] for c in integration.parse_body(resp, "contacts") or []}
    customer_ids: list[str] = []
    for name in CUSTOMERS:
        try:
            if name not in contacts:
                r = await integration.request("POST", "/contacts", json={"contact_name": name, "contact_type": "customer"})
                contacts[name] = integration.parse_body(r, "contact")["contact_id"]
                print(f"customer {name} created")
            customer_ids.append(contacts[name])
        except ConnectorError as exc:
            print(f"customer {name} failed: {exc.message}", file=sys.stderr)

    created_orders = 0
    if orders_existing:
        print(f"{len(orders_existing)} sales orders already exist, not creating more")
    else:
        for cust_idx, lines, days_ago in ORDERS:
            if cust_idx >= len(customer_ids):
                continue
            line_items = [{"item_id": item_ids[sku], "quantity": qty} for sku, qty in lines if sku in item_ids]
            if not line_items:
                continue
            body = {"customer_id": customer_ids[cust_idx], "date": (date.today() - timedelta(days=days_ago)).isoformat(),
                    "line_items": line_items}
            try:
                r = await integration.request("POST", "/salesorders", json=body)
                so = integration.parse_body(r, "salesorder")
                created_orders += 1
                print(f"sales order {so['salesorder_number']} for {CUSTOMERS[cust_idx]} ({so['status']})")
            except ConnectorError as exc:
                print(f"sales order failed: {exc.message}", file=sys.stderr)

    print(f"done: {len(item_ids)} items ready, {len(customer_ids)} customers, {created_orders} orders created")
    return 0 if item_ids else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
