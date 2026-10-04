"""Integration constants. Display names route auth; route names appear in URLs, env vars and tool names."""

FRESHDESK = "Freshdesk"
ZOHO_INVENTORY = "Zoho Inventory"

# URL slug / config key  ->  display name
IntegrationsRouteNameMapping: dict[str, str] = {
    "freshdesk": FRESHDESK,
    "zoho_inventory": ZOHO_INVENTORY,
}

IntegrationsDisplayToRoute: dict[str, str] = {v: k for k, v in IntegrationsRouteNameMapping.items()}

# Integrations that authenticate with user-supplied credentials (API key) instead of OAuth.
IntegrationsWithFormAuthOnly: list[str] = [FRESHDESK]

# Extra parameters an integration needs beyond its credentials.
IntegrationsWithAdditionalParams: dict[str, list[str]] = {
    ZOHO_INVENTORY: ["dc", "organization_id"],
}

# Task registry. Equivalent of INTEGRATION_FUNCTIONS in task_node.rs.
# Pattern: tasks.{route_name}.{operation}
INTEGRATION_FUNCTIONS: list[str] = [
    "tasks.freshdesk.verify_connection",
    "tasks.freshdesk.list_tickets",
    "tasks.freshdesk.get_ticket",
    "tasks.freshdesk.search_tickets",
    "tasks.zoho_inventory.verify_connection",
    "tasks.zoho_inventory.list_organizations",
    "tasks.zoho_inventory.list_items",
    "tasks.zoho_inventory.get_item",
    "tasks.zoho_inventory.search_items",
    "tasks.zoho_inventory.list_sales_orders",
    "tasks.zoho_inventory.get_sales_order",
]


def parse_task_name(task_fn: str) -> tuple[str, str]:
    """'tasks.freshdesk.list_tickets' -> ('freshdesk', 'list_tickets')."""
    parts = task_fn.split(".")
    if len(parts) != 3 or parts[0] != "tasks":
        raise ValueError(f"invalid task name: {task_fn}")
    return parts[1], parts[2]


def tasks_for_integration(route_name: str) -> list[str]:
    pairs = (parse_task_name(fn) for fn in INTEGRATION_FUNCTIONS)
    return [task for route, task in pairs if route == route_name]
