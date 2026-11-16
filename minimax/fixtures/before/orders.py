"""Order read service. Monetary amounts are integer cents."""


def list_orders(orders, *, status=None, min_total=None):
    """Return orders in their original order, optionally filtered by status."""
    if status is not None and status not in {"pending", "paid", "cancelled"}:
        raise ValueError("invalid status")
    return [dict(order) for order in orders if status is None or order["status"] == status]
