def list_orders(orders, *, status=None, min_total=None):
    if status is not None and status not in {"pending", "paid", "cancelled"}:
        raise ValueError("invalid status")
    if min_total is not None and (
        isinstance(min_total, bool)
        or not isinstance(min_total, int)
        or min_total < 0
    ):
        raise ValueError("invalid min_total")
    return [
        dict(order)
        for order in orders
        if (status is None or order["status"] == status)
        and (min_total is None or order["total"] >= min_total)
    ]
