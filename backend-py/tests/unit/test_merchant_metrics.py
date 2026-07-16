"""Unit tests for the pure «Мерчанты» stats helpers (bucket bounds + ratios)."""
from app.modules.stats.merchant_metrics import (
    CHECK_SIZE_EDGES,
    build_merchant_row,
    check_size_bounds,
    check_size_labels,
)


def test_check_size_bounds_has_13_contiguous_left_inclusive_buckets():
    bounds = check_size_bounds()
    assert len(bounds) == 13
    # Head bucket is [0, 1000).
    assert bounds[0] == (0, 1000)
    # Tail bucket is open-ended [20000, +inf).
    assert bounds[-1] == (20000, None)
    # Contiguous: each hi equals the next lo (left-incl / right-excl).
    for (lo, hi), (next_lo, _next_hi) in zip(bounds, bounds[1:]):
        assert hi == next_lo
    # Every concrete edge appears exactly as a boundary.
    assert tuple(hi for _lo, hi in bounds[:-1]) == CHECK_SIZE_EDGES


def test_check_size_labels_align_with_bounds():
    labels = check_size_labels()
    assert len(labels) == 13
    assert labels[0] == "<1000"
    assert labels[1] == "1000–2000"
    assert labels[-1] == "20000+"


def test_build_row_computes_conversion_and_payout():
    row = build_merchant_row(
        merchant_id=5,
        merchant_login="acme",
        requests=200,
        orders_created=150,
        orders_success=120,
        buckets=[0] * 13,
    )
    assert row["conversion_pct"] == 80.0   # 120 / 150
    assert row["payout_pct"] == 75.0       # 150 / 200
    assert row["merchant_login"] == "acme"
    assert len(row["check_size_buckets"]) == 13


def test_build_row_zero_denominators_do_not_divide_by_zero():
    row = build_merchant_row(
        merchant_id=1, merchant_login=None,
        requests=0, orders_created=0, orders_success=0, buckets=[0] * 13,
    )
    assert row["conversion_pct"] == 0.0
    assert row["payout_pct"] == 0.0
    assert row["merchant_login"] is None


def test_build_row_coerces_bucket_values_to_float():
    row = build_merchant_row(
        merchant_id=1, merchant_login="m",
        requests=10, orders_created=5, orders_success=3,
        buckets=[None, 1500, "2500"] + [0] * 10,
    )
    assert row["check_size_buckets"][0] == 0.0
    assert row["check_size_buckets"][1] == 1500.0
    assert row["check_size_buckets"][2] == 2500.0
