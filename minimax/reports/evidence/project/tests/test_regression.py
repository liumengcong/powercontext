import copy

import pytest

from business.orders import list_orders


def test_orders_default_unchanged(orders):
    assert list_orders(orders) == orders


def test_orders_status_filter(orders):
    assert [o["id"] for o in list_orders(orders, status="paid")] == ["A", "C", "E"]


def test_orders_invalid_status(orders):
    with pytest.raises(ValueError, match="status"):
        list_orders(orders, status="unknown")


def test_orders_empty():
    assert list_orders([]) == []


def test_orders_independent_copies(orders):
    original = copy.deepcopy(orders)
    result = list_orders(orders)
    result[0]["total"] = -1
    assert orders == original


def test_orders_none_filter(orders):
    assert list_orders(orders, status=None, min_total=None) == orders
