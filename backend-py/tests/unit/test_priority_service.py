from decimal import Decimal
import pytest
from app.modules.requisites.priority_service import PriorityService


def _scores(weights, base):
    # weights: list of (id, weight); id == weight-index for readability
    return PriorityService.compute_scores(list(enumerate(weights)), Decimal(base))


@pytest.mark.parametrize("weights,base,expected", [
    ([2, 1, 1], 100, [Decimal("150"), Decimal("75"), Decimal("75")]),      # 2x+x+x=300
    ([1, 2, 3], 100, [Decimal("50"), Decimal("100"), Decimal("150")]),     # 3x+2x+x=300
    ([1, 1, 1], 100, [Decimal("100"), Decimal("100"), Decimal("100")]),    # all equal → base
    ([1, 1, 1], 150, [Decimal("150"), Decimal("150"), Decimal("150")]),    # admin 50%
    ([1, 1, 1], 200, [Decimal("200"), Decimal("200"), Decimal("200")]),    # admin 100%
    ([2, 1],    150, [Decimal("200"), Decimal("100")]),                    # base 150, Σ3 → x=100
])
def test_compute_scores(weights, base, expected):
    got = _scores(weights, base)
    assert [got[i] for i in range(len(weights))] == expected


def test_compute_scores_empty_or_zero():
    assert PriorityService.compute_scores([], Decimal(100)) == {}


def test_base_for_percent():
    assert PriorityService.base_for_percent(Decimal(0)) == Decimal(100)
    assert PriorityService.base_for_percent(Decimal(50)) == Decimal(150)
    assert PriorityService.base_for_percent(Decimal(100)) == Decimal(200)
    assert PriorityService.base_for_percent(Decimal(-100)) == Decimal(0)   # clamped
    assert PriorityService.base_for_percent(Decimal(-200)) == Decimal(0)   # clamped
