import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def orders():
    return [
        {"id": "A", "status": "paid", "total": 0},
        {"id": "B", "status": "pending", "total": 999},
        {"id": "C", "status": "paid", "total": 1000},
        {"id": "D", "status": "cancelled", "total": 2500},
        {"id": "E", "status": "paid", "total": 5000},
    ]
