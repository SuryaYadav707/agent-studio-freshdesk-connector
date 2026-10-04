"""Zoho Inventory response normalisation. Keeps the fields an agent needs and
drops the hundreds Zoho returns."""
from __future__ import annotations

from typing import Any

ITEM_SORT_COLUMNS = ("name", "sku", "rate", "purchase_rate", "created_time", "last_modified_time", "reorder_level", "stock_on_hand")
ITEM_FILTERS = (
    "Status.All", "Status.Active", "Status.Inactive", "Status.Lowstock",
    "ItemType.All", "ItemType.Sales", "ItemType.Purchases", "ItemType.SalesAndPurchases",
    "ItemType.Inventory", "ItemType.NonInventory", "ItemType.Service",
)
SALES_ORDER_SORT_COLUMNS = ("customer_name", "salesorder_number", "shipment_date", "total", "date", "created_time", "last_modified_time")
SALES_ORDER_FILTERS = (
    "Status.All", "Status.Draft", "Status.Open", "Status.Invoiced", "Status.PartiallyInvoiced",
    "Status.Void", "Status.Closed",
)


def _num(value: Any) -> float | int | None:
    if value in (None, ""):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return int(f) if f.is_integer() else f


def normalize_organization(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "organization_id": str(raw.get("organization_id")),
        "name": raw.get("name"),
        "is_default_org": raw.get("is_default_org"),
        "currency_code": raw.get("currency_code"),
        "time_zone": raw.get("time_zone"),
        "plan_name": raw.get("plan_name"),
        "is_org_active": raw.get("is_org_active"),
    }


def normalize_item(raw: dict[str, Any], *, detail: bool = False) -> dict[str, Any]:
    stock = _num(raw.get("stock_on_hand"))
    reorder = _num(raw.get("reorder_level"))
    item: dict[str, Any] = {
        "item_id": str(raw.get("item_id")),
        "name": raw.get("name"),
        "sku": raw.get("sku"),
        "status": raw.get("status"),
        "unit": raw.get("unit"),
        "item_type": raw.get("item_type"),
        "product_type": raw.get("product_type"),
        "rate": _num(raw.get("rate")),
        "purchase_rate": _num(raw.get("purchase_rate")),
        "stock_on_hand": stock,
        "available_stock": _num(raw.get("available_stock")),
        "actual_available_stock": _num(raw.get("actual_available_stock")),
        "reorder_level": reorder,
        "is_low_stock": (stock is not None and reorder is not None and reorder > 0 and stock <= reorder),
        "tax_name": raw.get("tax_name"),
        "created_time": raw.get("created_time"),
        "last_modified_time": raw.get("last_modified_time"),
    }
    if detail:
        item["description"] = raw.get("description")
        item["brand"] = raw.get("brand")
        item["manufacturer"] = raw.get("manufacturer")
        item["is_returnable"] = raw.get("is_returnable")
        locations = raw.get("locations") or raw.get("warehouses") or []
        item["locations"] = [
            {
                "location_id": str(loc.get("location_id") or loc.get("warehouse_id")),
                "location_name": loc.get("location_name") or loc.get("warehouse_name"),
                "stock_on_hand": _num(loc.get("location_stock_on_hand", loc.get("warehouse_stock_on_hand"))),
                "available_stock": _num(loc.get("location_available_stock", loc.get("warehouse_available_stock"))),
                "actual_available_stock": _num(loc.get("location_actual_available_stock", loc.get("warehouse_actual_available_stock"))),
            }
            for loc in locations
            if isinstance(loc, dict)
        ]
    return item


def normalize_sales_order(raw: dict[str, Any], *, detail: bool = False) -> dict[str, Any]:
    order: dict[str, Any] = {
        "salesorder_id": str(raw.get("salesorder_id")),
        "salesorder_number": raw.get("salesorder_number"),
        "date": raw.get("date"),
        "status": raw.get("status"),
        "order_status": raw.get("order_status"),
        "invoiced_status": raw.get("invoiced_status"),
        "paid_status": raw.get("paid_status"),
        "shipped_status": raw.get("shipped_status"),
        "customer_id": str(raw.get("customer_id")) if raw.get("customer_id") else None,
        "customer_name": raw.get("customer_name"),
        "total": _num(raw.get("total")),
        "currency_code": raw.get("currency_code"),
        "shipment_date": raw.get("shipment_date"),
        "reference_number": raw.get("reference_number"),
        "created_time": raw.get("created_time"),
        "last_modified_time": raw.get("last_modified_time"),
    }
    if detail:
        order["sub_total"] = _num(raw.get("sub_total"))
        order["tax_total"] = _num(raw.get("tax_total"))
        order["discount"] = _num(raw.get("discount"))
        order["notes"] = raw.get("notes")
        order["delivery_method"] = raw.get("delivery_method")
        order["line_items"] = [
            {
                "line_item_id": str(li.get("line_item_id")),
                "item_id": str(li.get("item_id")),
                "name": li.get("name"),
                "sku": li.get("sku"),
                "quantity": _num(li.get("quantity")),
                "quantity_invoiced": _num(li.get("quantity_invoiced")),
                "quantity_shipped": _num(li.get("quantity_shipped")),
                "rate": _num(li.get("rate")),
                "item_total": _num(li.get("item_total")),
            }
            for li in raw.get("line_items") or []
            if isinstance(li, dict)
        ]
        for addr_key in ("billing_address", "shipping_address"):
            addr = raw.get(addr_key)
            if isinstance(addr, dict):
                order[addr_key] = {k: addr.get(k) for k in ("address", "city", "state", "zip", "country")}
    return order


def page_context(raw: dict[str, Any]) -> tuple[int, int, bool]:
    ctx = raw.get("page_context") or {}
    return int(ctx.get("page", 1)), int(ctx.get("per_page", 0) or 0), bool(ctx.get("has_more_page", False))
