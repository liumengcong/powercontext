import copy

import pytest

from business.orders import list_orders


def test_min_total_boundary(orders):
    assert [o["id"] for o in list_orders(orders, min_total=1000)] == ["C", "D", "E"]


def test_min_total_combines_status(orders):
    assert [o["id"] for o in list_orders(orders, status="paid", min_total=1000)] == ["C", "E"]


@pytest.mark.parametrize("invalid", [-1, True, False, 1.5, "1000", float("nan"), float("inf")])
def test_min_total_rejects_invalid(orders, invalid):
    with pytest.raises(ValueError, match="min_total"):
        list_orders(orders, min_total=invalid)


def test_min_total_zero(orders):
    assert list_orders(orders, min_total=0) == orders


def test_min_total_above_all(orders):
    assert list_orders(orders, min_total=6000) == []


def test_min_total_does_not_mutate(orders):
    before = copy.deepcopy(orders)
    result = list_orders(orders, min_total=1000)
    assert orders == before
    result[0]["id"] = "changed"
    assert orders == before
