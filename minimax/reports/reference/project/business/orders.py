"""Order read service. Monetary amounts are integer cents."""


def list_orders(orders, *, status=None, min_total=None):
    """Return copies matching status and the inclusive minimum amount in cents."""
    if status is not None and status not in {"pending", "paid", "cancelled"}:
        raise ValueError("invalid status")
    if min_total is not None and (type(min_total) is not int or min_total < 0):
        raise ValueError("min_total must be a non-negative integer number of cents")
    return [
        dict(order)
        for order in orders
        if (status is None or order["status"] == status)
        and (min_total is None or order["total"] >= min_total)
    ]
