import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy.ext.asyncio import AsyncSession
from app.modules.pooling.service import PoolingService, PoolingResult
from app.common.enums.pooling import PoolingStrategy
from app.common.enums.finances import Currency
from app.common.enums.payments import PaymentMethod
from app.common.enums.requisites import RequisiteStatus
from app.common.enums.traders import TraderStatus
from app.modules.orders.models import Order
from app.modules.requisites.models import Requisite, RequisiteLimit
from app.modules.traders.models import Trader
# Importing related models so SQLAlchemy can resolve string-based relationships
# referenced by Order/Requisite mappers when test isolation prevents them from
# being loaded indirectly.
from app.modules.payments.models import PaymentOption  # noqa: F401
from app.modules.users.models import User  # noqa: F401
from app.modules.merchants.models import Merchant  # noqa: F401

@pytest.fixture
def mock_session():
    return AsyncMock(spec=AsyncSession)

@pytest.fixture
def pooling_service(mock_session):
    return PoolingService(mock_session)

@pytest.fixture
def mock_order():
    order = MagicMock(spec=Order)
    order.currency = Currency.RUB
    order.amount = 1000.0
    order.amount_usdt = Decimal("10.0")
    order.payment_method = "card"
    order.payment_option_id = None
    # No merchant binding filter unless a test explicitly sets a merchant_id.
    order.merchant_id = None
    return order


# ── select_requisite (original method) ──────────────────────

@pytest.mark.asyncio
async def test_select_requisite_random(pooling_service, mock_session, mock_order):
    mock_req_1 = MagicMock(spec=Requisite, id=1)
    mock_req_2 = MagicMock(spec=Requisite, id=2)
    
    mock_result = MagicMock()
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = [mock_req_1, mock_req_2]
    mock_result.scalars.return_value = mock_scalars
    mock_session.execute.return_value = mock_result
    
    with patch("app.modules.pooling.service.random.choice", return_value=mock_req_1) as mock_choice:
        result = await pooling_service.select_requisite(mock_order, PoolingStrategy.RANDOM)
        
        assert result == mock_req_1
        mock_session.execute.assert_called_once()
        mock_choice.assert_called_once_with([mock_req_1, mock_req_2])

@pytest.mark.asyncio
async def test_select_requisite_lru(pooling_service, mock_session, mock_order):
    mock_req = MagicMock(spec=Requisite, id=1)
    
    mock_result = MagicMock()
    mock_scalars = MagicMock()
    mock_scalars.first.return_value = mock_req
    mock_result.scalars.return_value = mock_scalars
    mock_session.execute.return_value = mock_result
    
    result = await pooling_service.select_requisite(mock_order, PoolingStrategy.LEAST_RECENTLY_USED)
    
    assert result == mock_req
    mock_session.execute.assert_called_once()
    
    stmt = mock_session.execute.call_args[0][0]
    stmt_str = str(stmt)
    assert "ORDER BY" in stmt_str
    assert "last_used_at" in stmt_str

@pytest.mark.asyncio
async def test_select_requisite_none_found(pooling_service, mock_session, mock_order):
    mock_result = MagicMock()
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = []
    mock_result.scalars.return_value = mock_scalars
    mock_session.execute.return_value = mock_result
    
    result = await pooling_service.select_requisite(mock_order, PoolingStrategy.RANDOM)
    
    assert result is None
    mock_session.execute.assert_called_once()

@pytest.mark.asyncio
async def test_select_requisite_unsupported_strategy(pooling_service, mock_session, mock_order):
    result = await pooling_service.select_requisite(mock_order, "UNSUPPORTED_STRATEGY")

    assert result is None
    mock_session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_select_requisite_weighted(pooling_service, mock_session, mock_order):
    """WEIGHTED strategy must call random.choices with the REAL fetched
    requisites and weights derived from their (distinct) priority_score
    values — not e.g. random.choice, and not uniform/fabricated weights."""
    mock_req_1 = MagicMock(spec=Requisite, id=1, priority_score=Decimal("300"))
    mock_req_2 = MagicMock(spec=Requisite, id=2, priority_score=Decimal("100"))

    mock_result = MagicMock()
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = [mock_req_1, mock_req_2]
    mock_result.scalars.return_value = mock_scalars
    mock_session.execute.return_value = mock_result

    with patch("app.modules.pooling.service.random.choices", return_value=[mock_req_2]) as mock_choices, \
         patch("app.modules.pooling.service.random.choice") as mock_choice:
        result = await pooling_service.select_requisite(mock_order, PoolingStrategy.WEIGHTED)

        assert result == mock_req_2
        mock_session.execute.assert_called_once()
        mock_choices.assert_called_once_with([mock_req_1, mock_req_2], weights=[300.0, 100.0], k=1)
        # Non-zero weights → must NOT fall back to the uniform random.choice.
        mock_choice.assert_not_called()


@pytest.mark.asyncio
async def test_select_requisite_weighted_uniform_fallback(pooling_service, mock_session, mock_order):
    """When every candidate's priority_score is 0 the weighted branch must
    fall back to a uniform random.choice (sum(weights) == 0 guard) instead of
    calling random.choices with all-zero weights."""
    mock_req_1 = MagicMock(spec=Requisite, id=1, priority_score=Decimal("0"))
    mock_req_2 = MagicMock(spec=Requisite, id=2, priority_score=Decimal("0"))

    mock_result = MagicMock()
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = [mock_req_1, mock_req_2]
    mock_result.scalars.return_value = mock_scalars
    mock_session.execute.return_value = mock_result

    with patch("app.modules.pooling.service.random.choices") as mock_choices, \
         patch("app.modules.pooling.service.random.choice", return_value=mock_req_2) as mock_choice:
        result = await pooling_service.select_requisite(mock_order, PoolingStrategy.WEIGHTED)

        assert result == mock_req_2
        mock_choice.assert_called_once_with([mock_req_1, mock_req_2])
        mock_choices.assert_not_called()

def test_base_query_filters(pooling_service, mock_order):
    stmt = pooling_service._get_base_query(mock_order)
    stmt_str = str(stmt)
    
    assert "is_active" in stmt_str
    assert "is_archived" in stmt_str
    assert "status" in stmt_str
    assert "currency" in stmt_str
    assert "payment_method" in stmt_str
    assert "limit_min_transaction" in stmt_str
    assert "limit_max_transaction" in stmt_str
    assert "current_daily_turnover" in stmt_str
    assert "current_monthly_turnover" in stmt_str
    # No payment_option WHERE filter when order has no preferred bank
    assert "requisites.payment_option_id =" not in stmt_str


def test_base_query_filters_by_payment_option(pooling_service, mock_order):
    """When order specifies payment_option_id, requisites should be filtered strictly."""
    mock_order.payment_option_id = 7
    stmt = pooling_service._get_base_query(mock_order)
    stmt_str = str(stmt)
    assert "requisites.payment_option_id =" in stmt_str


def test_base_query_skips_merchant_filter_when_no_merchant(pooling_service, mock_order):
    """No merchant_id on the order — clause for merchant binding shouldn't be added."""
    assert mock_order.merchant_id is None
    stmt = pooling_service._get_base_query(mock_order)
    stmt_str = str(stmt)
    # Helper-tables should not appear when filter is skipped
    assert "trader_merchants" not in stmt_str
    assert "merchant_trader_groups" not in stmt_str


def test_base_query_applies_merchant_filter(pooling_service, mock_order):
    """When order has merchant_id, the SQL must reference the binding tables."""
    mock_order.merchant_id = 42
    stmt = pooling_service._get_base_query(mock_order)
    stmt_str = str(stmt)
    assert "trader_merchants" in stmt_str
    assert "merchant_trader_groups" in stmt_str
    assert "accept_all_merchants" in stmt_str


def test_check_exclusion_payment_option_mismatch():
    req = _make_requisite(payment_option_id=1)
    limits = _make_limits()
    result = PoolingService._check_exclusion(
        req, limits, Decimal("1000"), 0,
        trader=_make_trader(),
        order_payment_option_id=2,
    )
    assert "payment_option_mismatch" in result


def test_check_exclusion_payment_option_match():
    req = _make_requisite(payment_option_id=5)
    limits = _make_limits()
    result = PoolingService._check_exclusion(
        req, limits, Decimal("1000"), 0,
        trader=_make_trader(),
        order_payment_option_id=5,
    )
    assert result is None


# ── _check_exclusion ────────────────────────────────────────

def _make_requisite(**kwargs):
    defaults = {
        "id": 1,
        "trader_id": 10,
        "bank_name": "Test Bank",
        "payment_method": PaymentMethod.CARD,
        "status": RequisiteStatus.ENABLED,
        "is_active": True,
        "is_archived": False,
        "currency": Currency.RUB,
        "last_used_at": None,
        "payment_option_id": None,
    }
    defaults.update(kwargs)
    req = MagicMock(spec=Requisite)
    for k, v in defaults.items():
        setattr(req, k, v)
    return req


def _make_trader(**kwargs):
    defaults = {
        "id": 1,
        "user_id": 10,
        "status": TraderStatus.ENABLED,
        "is_payin_active": True,
        "is_payout_active": True,
        "accept_all_merchants": False,
    }
    defaults.update(kwargs)
    trader = MagicMock(spec=Trader)
    for k, v in defaults.items():
        setattr(trader, k, v)
    return trader


def _make_limits(**kwargs):
    defaults = {
        "limit_min_transaction": Decimal("100"),
        "limit_max_transaction": Decimal("50000"),
        "limit_daily": Decimal("100000"),
        "limit_monthly": Decimal("1000000"),
        "limit_max_concurrent_orders": None,
        "current_daily_turnover": Decimal("0"),
        "current_monthly_turnover": Decimal("0"),
    }
    defaults.update(kwargs)
    lim = MagicMock(spec=RequisiteLimit)
    for k, v in defaults.items():
        setattr(lim, k, v)
    return lim


def test_check_exclusion_none_when_eligible():
    req = _make_requisite()
    limits = _make_limits()
    assert PoolingService._check_exclusion(req, limits, Decimal("1000"), 0, trader=_make_trader()) is None


def test_check_exclusion_inactive():
    req = _make_requisite(is_active=False)
    limits = _make_limits()
    assert "is_active=false" in PoolingService._check_exclusion(req, limits, Decimal("1000"), 0, trader=_make_trader())


def test_check_exclusion_archived():
    req = _make_requisite(is_archived=True)
    limits = _make_limits()
    assert "is_archived=true" in PoolingService._check_exclusion(req, limits, Decimal("1000"), 0, trader=_make_trader())


def test_check_exclusion_status_disabled():
    req = _make_requisite(status=RequisiteStatus.DISABLED)
    limits = _make_limits()
    result = PoolingService._check_exclusion(req, limits, Decimal("1000"), 0, trader=_make_trader())
    assert "status=disabled" in result


def test_check_exclusion_no_limits():
    req = _make_requisite()
    assert "no_limits_configured" in PoolingService._check_exclusion(req, None, Decimal("1000"), 0, trader=_make_trader())


def test_check_exclusion_amount_below_min():
    req = _make_requisite()
    limits = _make_limits(limit_min_transaction=Decimal("5000"))
    result = PoolingService._check_exclusion(req, limits, Decimal("1000"), 0, trader=_make_trader())
    assert "amount_below_min" in result


def test_check_exclusion_amount_above_max():
    req = _make_requisite()
    limits = _make_limits(limit_max_transaction=Decimal("500"))
    result = PoolingService._check_exclusion(req, limits, Decimal("1000"), 0, trader=_make_trader())
    assert "amount_above_max" in result


def test_check_exclusion_daily_limit():
    req = _make_requisite()
    limits = _make_limits(current_daily_turnover=Decimal("99500"), limit_daily=Decimal("100000"))
    result = PoolingService._check_exclusion(req, limits, Decimal("1000"), 0, trader=_make_trader())
    assert "daily_limit_exceeded" in result


def test_check_exclusion_monthly_limit():
    req = _make_requisite()
    limits = _make_limits(current_monthly_turnover=Decimal("999500"), limit_monthly=Decimal("1000000"))
    result = PoolingService._check_exclusion(req, limits, Decimal("1000"), 0, trader=_make_trader())
    assert "monthly_limit_exceeded" in result


def test_check_exclusion_concurrent_orders():
    req = _make_requisite()
    limits = _make_limits(limit_max_concurrent_orders=3)
    result = PoolingService._check_exclusion(req, limits, Decimal("1000"), 3, trader=_make_trader())
    assert "concurrent_limit" in result


def test_check_exclusion_concurrent_orders_ok():
    req = _make_requisite()
    limits = _make_limits(limit_max_concurrent_orders=3)
    assert PoolingService._check_exclusion(req, limits, Decimal("1000"), 2, trader=_make_trader()) is None


def test_check_exclusion_trader_blocked():
    req = _make_requisite()
    limits = _make_limits()
    trader = _make_trader(status=TraderStatus.BLOCKED)
    result = PoolingService._check_exclusion(req, limits, Decimal("1000"), 0, trader=trader)
    assert "trader_status=blocked" in result


def test_check_exclusion_trader_payin_inactive():
    req = _make_requisite()
    limits = _make_limits()
    trader = _make_trader(is_payin_active=False)
    result = PoolingService._check_exclusion(req, limits, Decimal("1000"), 0, trader=trader)
    assert "trader_payin_inactive" in result


def test_check_exclusion_trader_missing():
    req = _make_requisite()
    limits = _make_limits()
    result = PoolingService._check_exclusion(req, limits, Decimal("1000"), 0, trader=None)
    assert "trader_profile_missing" in result


# ── _check_exclusion: merchant↔trader binding ────────────────

def test_check_exclusion_trader_in_allowed_set_passes():
    """Trader directly bound to merchant — eligible regardless of merchant_has_bindings."""
    req = _make_requisite()
    limits = _make_limits()
    trader = _make_trader(id=7)
    assert PoolingService._check_exclusion(
        req, limits, Decimal("1000"), 0, trader=trader,
        allowed_trader_ids={7}, merchant_has_bindings=True,
    ) is None


def test_check_exclusion_not_assigned_to_merchant():
    """Merchant has bindings but trader isn't among them — excluded."""
    req = _make_requisite()
    limits = _make_limits()
    trader = _make_trader(id=99)
    result = PoolingService._check_exclusion(
        req, limits, Decimal("1000"), 0, trader=trader,
        allowed_trader_ids={1, 2, 3}, merchant_has_bindings=True,
    )
    assert "not_assigned_to_merchant" in result


def test_check_exclusion_trader_not_open_to_unassigned_merchant():
    """Merchant has no bindings; trader is closed (accept_all_merchants=False) — excluded."""
    req = _make_requisite()
    limits = _make_limits()
    trader = _make_trader(id=99, accept_all_merchants=False)
    result = PoolingService._check_exclusion(
        req, limits, Decimal("1000"), 0, trader=trader,
        allowed_trader_ids=set(), merchant_has_bindings=False,
    )
    assert "trader_not_open_to_unassigned_merchants" in result


def test_check_exclusion_open_trader_passes_for_unassigned_merchant():
    """Merchant has no bindings; trader has accept_all_merchants=True — eligible."""
    req = _make_requisite()
    limits = _make_limits()
    trader = _make_trader(id=99, accept_all_merchants=True)
    assert PoolingService._check_exclusion(
        req, limits, Decimal("1000"), 0, trader=trader,
        allowed_trader_ids=set(), merchant_has_bindings=False,
    ) is None


def test_check_exclusion_skips_when_no_filter_passed():
    """Backwards compatibility — no merchant filter info means no exclusion."""
    req = _make_requisite()
    limits = _make_limits()
    trader = _make_trader(id=99, accept_all_merchants=False)
    # Не передаём allowed_trader_ids → проверка пропускается
    assert PoolingService._check_exclusion(
        req, limits, Decimal("1000"), 0, trader=trader,
    ) is None


# ── _serialize_requisite ─────────────────────────────────────

def test_serialize_requisite_with_limits():
    req = _make_requisite(id=7, trader_id=20, priority_score=Decimal("150"))
    limits = _make_limits(
        limit_min_transaction=Decimal("100"),
        limit_max_transaction=Decimal("50000"),
        limit_daily=Decimal("100000"),
        current_daily_turnover=Decimal("5000"),
    )
    data = PoolingService._serialize_requisite(req, limits, 2)

    assert data["id"] == 7
    assert data["user_id"] == 20
    assert data["active_orders"] == 2
    assert data["priority_score"] == 150.0
    assert data["limits"]["limit_min_transaction"] == 100.0
    assert data["limits"]["current_daily_turnover"] == 5000.0


def test_serialize_requisite_without_limits():
    req = _make_requisite()
    data = PoolingService._serialize_requisite(req, None, 0)
    assert data["limits"] is None


# ── _resolve_merchant_access ─────────────────────────────────

def _make_rows_result(rows):
    """Async-friendly mock of session.execute(...) result that supports .all()/.first()."""
    res = MagicMock()
    res.all.return_value = rows
    res.first.return_value = rows[0] if rows else None
    return res


@pytest.mark.asyncio
async def test_resolve_merchant_access_returns_none_for_none_merchant(pooling_service, mock_session):
    """When merchant_id is None, return (None, False) so _check_exclusion skips the filter entirely."""
    allowed, has_bindings = await pooling_service._resolve_merchant_access(None)
    assert allowed is None
    assert has_bindings is False
    mock_session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_merchant_access_with_direct_only(pooling_service, mock_session):
    mock_session.execute = AsyncMock(side_effect=[
        _make_rows_result([(1,), (2,)]),  # direct trader_merchants
        _make_rows_result([]),            # via_groups
        _make_rows_result([]),            # has_group_binding
    ])

    allowed, has_bindings = await pooling_service._resolve_merchant_access(42)

    assert allowed == {1, 2}
    assert has_bindings is True


@pytest.mark.asyncio
async def test_resolve_merchant_access_with_groups_only(pooling_service, mock_session):
    mock_session.execute = AsyncMock(side_effect=[
        _make_rows_result([]),            # direct
        _make_rows_result([(7,), (8,)]),  # via_groups
        _make_rows_result([(1,)]),        # has_group_binding (1 row → True)
    ])

    allowed, has_bindings = await pooling_service._resolve_merchant_access(42)

    assert allowed == {7, 8}
    assert has_bindings is True


@pytest.mark.asyncio
async def test_resolve_merchant_access_no_bindings(pooling_service, mock_session):
    mock_session.execute = AsyncMock(side_effect=[
        _make_rows_result([]),
        _make_rows_result([]),
        _make_rows_result([]),
    ])

    allowed, has_bindings = await pooling_service._resolve_merchant_access(42)

    assert allowed == set()
    assert has_bindings is False


@pytest.mark.asyncio
async def test_resolve_merchant_access_union_direct_and_groups(pooling_service, mock_session):
    mock_session.execute = AsyncMock(side_effect=[
        _make_rows_result([(1,), (5,)]),
        _make_rows_result([(5,), (9,)]),  # 5 is duplicated — should dedupe via set()
        _make_rows_result([(1,)]),
    ])

    allowed, has_bindings = await pooling_service._resolve_merchant_access(42)

    assert allowed == {1, 5, 9}
    assert has_bindings is True


# ── select_requisite_with_diagnostics ────────────────────────

@pytest.mark.asyncio
async def test_diagnostics_candidates_and_excluded(pooling_service, mock_session, mock_order):
    good_req = _make_requisite(id=1)
    good_limits = _make_limits()
    bad_req = _make_requisite(id=2, is_active=False)
    bad_limits = _make_limits()

    trader = _make_trader()
    mock_result = MagicMock()
    mock_result.all.return_value = [
        (good_req, good_limits, 0, Decimal("1000"), Decimal("0"), trader),
        (bad_req, bad_limits, 0, Decimal("1000"), Decimal("0"), trader),
    ]
    mock_session.execute.return_value = mock_result

    result = await pooling_service.select_requisite_with_diagnostics(
        mock_order, PoolingStrategy.LEAST_RECENTLY_USED
    )

    assert isinstance(result, PoolingResult)
    assert len(result.candidates) == 1
    assert result.candidates[0]["id"] == 1
    assert result.selected == good_req


@pytest.mark.asyncio
async def test_diagnostics_no_candidates(pooling_service, mock_session, mock_order):
    bad_req = _make_requisite(id=5, status=RequisiteStatus.BLOCKED)
    bad_limits = _make_limits()

    mock_result = MagicMock()
    mock_result.all.return_value = [(bad_req, bad_limits, 0, Decimal("1000"), Decimal("0"), _make_trader())]
    mock_session.execute.return_value = mock_result

    result = await pooling_service.select_requisite_with_diagnostics(
        mock_order, PoolingStrategy.LEAST_RECENTLY_USED
    )

    assert result.selected is None
    assert len(result.candidates) == 0


@pytest.mark.asyncio
async def test_diagnostics_empty_results(pooling_service, mock_session, mock_order):
    mock_result = MagicMock()
    mock_result.all.return_value = []
    mock_session.execute.return_value = mock_result

    result = await pooling_service.select_requisite_with_diagnostics(
        mock_order, PoolingStrategy.RANDOM
    )

    assert result.selected is None
    assert result.candidates == []


@pytest.mark.asyncio
async def test_diagnostics_select_requisite_weighted(pooling_service, mock_session, mock_order):
    """WEIGHTED strategy in the diagnostics/sync-payin hot path: the weighted
    pick must use the candidates' real priority_score weights (patch
    random.choices and assert the actual call args), the returned
    PoolingResult.selected must be the ORM Requisite random.choices actually
    picked, and no extra query beyond the single main query may run."""
    req1 = _make_requisite(id=1, priority_score=Decimal("300"))
    req2 = _make_requisite(id=2, priority_score=Decimal("100"))
    limits = _make_limits()
    trader = _make_trader()

    mock_result = MagicMock()
    mock_result.all.return_value = [
        (req1, limits, 0, Decimal("1000"), Decimal("0"), trader),
        (req2, limits, 0, Decimal("1000"), Decimal("0"), trader),
    ]
    mock_session.execute.return_value = mock_result

    def _pick_second(candidates, weights, k):
        assert k == 1
        return [candidates[1]]

    with patch("app.modules.pooling.service.random.choices", side_effect=_pick_second) as mock_choices, \
         patch("app.modules.pooling.service.random.choice") as mock_choice:
        result = await pooling_service.select_requisite_with_diagnostics(
            mock_order, PoolingStrategy.WEIGHTED
        )

    mock_choices.assert_called_once()
    call_args, call_kwargs = mock_choices.call_args
    assert call_kwargs["weights"] == [300.0, 100.0]
    mock_choice.assert_not_called()

    assert result.selected == req2
    assert len(result.candidates) == 2
    # Only the single main pooling query — no extra query for the weighted pick.
    mock_session.execute.assert_called_once()


@pytest.mark.asyncio
async def test_diagnostics_select_requisite_weighted_uniform_fallback(
    pooling_service, mock_session, mock_order
):
    """Diagnostics path: all-zero priority_score candidates must fall back to
    a uniform random.choice over the candidate dicts, still yielding a
    selected requisite (no crash), and never call random.choices."""
    req1 = _make_requisite(id=1, priority_score=Decimal("0"))
    req2 = _make_requisite(id=2, priority_score=Decimal("0"))
    limits = _make_limits()
    trader = _make_trader()

    mock_result = MagicMock()
    mock_result.all.return_value = [
        (req1, limits, 0, Decimal("1000"), Decimal("0"), trader),
        (req2, limits, 0, Decimal("1000"), Decimal("0"), trader),
    ]
    mock_session.execute.return_value = mock_result

    with patch("app.modules.pooling.service.random.choices") as mock_choices, \
         patch("app.modules.pooling.service.random.choice", side_effect=lambda cands: cands[0]) as mock_choice:
        result = await pooling_service.select_requisite_with_diagnostics(
            mock_order, PoolingStrategy.WEIGHTED
        )

    mock_choices.assert_not_called()
    mock_choice.assert_called_once()
    assert result.selected is not None
    assert result.selected in (req1, req2)


@pytest.mark.asyncio
async def test_diagnostics_query_pushes_cheap_exclusions_to_sql(
    pooling_service, mock_session, mock_order
):
    """Perf guard: the hot-path query must filter the cheap, column-level
    exclusions in SQL (so we don't hydrate every LOCAL requisite and filter in
    Python). Trader/limit must be INNER joins; balance/active-orders stay OUTER.
    """
    mock_result = MagicMock()
    mock_result.all.return_value = []
    mock_session.execute.return_value = mock_result

    # merchant_id is None → _resolve_merchant_access returns early, so the only
    # execute() call is the main pooling query.
    await pooling_service.select_requisite_with_diagnostics(
        mock_order, PoolingStrategy.LEAST_RECENTLY_USED
    )

    stmt = mock_session.execute.call_args[0][0]
    sql = str(stmt)
    # cheap exclusions are now predicates in the query
    assert "is_active" in sql
    assert "is_archived" in sql
    assert "is_payin_active" in sql
    # trader profile / limits absence is itself an exclusion → INNER joined
    assert "LEFT OUTER JOIN traders" not in sql
    assert "LEFT OUTER JOIN requisite_limits" not in sql
    # balance & active-order aggregates remain OUTER (0/absent is valid)
    assert "LEFT OUTER JOIN balances" in sql
    # raiseload options suppress the Requisite/Trader lazy="selectin" fan-out
    # (the loop reads only columns) — guard against accidental removal.
    assert len(stmt._with_options) == 2


@pytest.mark.asyncio
async def test_diagnostics_pushes_merchant_acl_into_sql(pooling_service, mock_session, mock_order):
    """With a merchant_id, the merchant↔trader ACL must be IN the SQL (binding
    tables), not resolved via extra queries + Python filtering — so exactly ONE
    execute (the main pooling query), no `_resolve_merchant_access` fan-out."""
    mock_order.merchant_id = 42
    mock_result = MagicMock()
    mock_result.all.return_value = []
    mock_session.execute.return_value = mock_result

    await pooling_service.select_requisite_with_diagnostics(
        mock_order, PoolingStrategy.LEAST_RECENTLY_USED
    )

    # No 3-query _resolve_merchant_access fan-out — only the main query runs.
    assert mock_session.execute.call_count == 1
    sql = str(mock_session.execute.call_args[0][0])
    assert "trader_merchants" in sql
    assert "merchant_trader_groups" in sql
    assert "accept_all_merchants" in sql


@pytest.mark.asyncio
async def test_diagnostics_no_acl_tables_without_merchant(pooling_service, mock_session, mock_order):
    """No merchant_id → no binding-table clause in the SQL."""
    assert mock_order.merchant_id is None
    mock_result = MagicMock()
    mock_result.all.return_value = []
    mock_session.execute.return_value = mock_result

    await pooling_service.select_requisite_with_diagnostics(
        mock_order, PoolingStrategy.LEAST_RECENTLY_USED
    )
    assert mock_session.execute.call_count == 1
    sql = str(mock_session.execute.call_args[0][0])
    assert "trader_merchants" not in sql
    assert "merchant_trader_groups" not in sql


# ── compute_method_limits ──────────────────────────────────────────────────


def _row(floor, ceiling):
    """Mock per-requisite SQLAlchemy Row with `.floor` / `.ceiling` attrs."""
    r = MagicMock()
    r.floor = floor
    r.ceiling = ceiling
    return r


def _mock_rows(rows):
    """Wire the mock session so .execute(...).all() returns ``rows``."""
    result = MagicMock()
    result.all.return_value = rows
    return result


@pytest.mark.asyncio
async def test_compute_method_limits_disjoint_ranges_surface_gap(pooling_service, mock_session):
    """Two requisites with a gap → two ranges (the gap MUST stay visible —
    that's the whole point: the merchant should see "between max(A) and min(B)
    nobody can take the order")."""
    mock_session.execute.return_value = _mock_rows([
        _row(Decimal("4000"), Decimal("4500")),
        _row(Decimal("5000"), Decimal("10000")),
    ])
    out = await pooling_service.compute_method_limits(
        merchant_id=7, currency=Currency.RUB, payment_method=PaymentMethod.SBP, rate=Decimal("95.5"),
    )
    assert out == [
        {"min": Decimal("4000"), "max": Decimal("4500")},
        {"min": Decimal("5000"), "max": Decimal("10000")},
    ]


@pytest.mark.asyncio
async def test_compute_method_limits_overlapping_ranges_merge(pooling_service, mock_session):
    """Overlapping or touching intervals collapse into one — only TRUE gaps
    survive as separate entries."""
    mock_session.execute.return_value = _mock_rows([
        _row(Decimal("100"), Decimal("500")),
        _row(Decimal("400"), Decimal("800")),    # overlaps the first
        _row(Decimal("800"), Decimal("1000")),   # touches the merged tail
    ])
    out = await pooling_service.compute_method_limits(
        merchant_id=7, currency=Currency.RUB, payment_method=PaymentMethod.SBP, rate=Decimal("95.5"),
    )
    assert out == [{"min": Decimal("100"), "max": Decimal("1000")}]


@pytest.mark.asyncio
async def test_compute_method_limits_no_rows_returns_empty(pooling_service, mock_session):
    """No surviving requisite → empty list (bot renders "нет доступных
    реквизитов")."""
    mock_session.execute.return_value = _mock_rows([])
    out = await pooling_service.compute_method_limits(
        merchant_id=7, currency=Currency.RUB, payment_method=PaymentMethod.CARD, rate=Decimal("95.5"),
    )
    assert out == []


@pytest.mark.asyncio
async def test_compute_method_limits_query_shape(pooling_service, mock_session):
    """The query must enforce the canonical pooling gates AND merchant ACL +
    drop requisites with ceiling < floor; emits per-requisite rows ordered
    ascending (so the Python-side merge sees them in order)."""
    mock_session.execute.return_value = _mock_rows([])

    await pooling_service.compute_method_limits(
        merchant_id=7, currency=Currency.RUB, payment_method=PaymentMethod.SBP, rate=Decimal("95.5"),
    )
    sql = str(mock_session.execute.call_args[0][0]).lower()

    # Per-requisite ceiling expression + ordered emission (no aggregate).
    assert "least(" in sql
    assert "order by" in sql and "limit_min_transaction" in sql
    # Canonical gates (same as the boevoy _get_base_query)
    assert "requisites.is_active" in sql
    assert "requisites.is_archived" in sql
    assert "requisites.source" in sql
    assert "requisites.status" in sql
    assert "traders.status" in sql
    assert "traders.is_payin_active" in sql
    assert "requisites.payment_method" in sql
    # Trader balance (USDT) must be in the join + filter
    assert "balances" in sql
    # Merchant ACL — group binding (trader_group_members + merchant_trader_groups)
    assert "trader_merchants" in sql
    assert "merchant_trader_groups" in sql
    # Concurrent-orders cap — must mirror _get_base_query so /limit doesn't
    # show a requisite already at its concurrency ceiling.
    assert "limit_max_concurrent_orders" in sql
    assert "active_count" in sql


def test_merge_intervals_helper_edge_cases():
    """Pure helper — direct checks on edge cases (empty, single, nested, far)."""
    from app.modules.pooling.service import _merge_intervals
    assert _merge_intervals([]) == []
    assert _merge_intervals([(Decimal("100"), Decimal("200"))]) == [
        {"min": Decimal("100"), "max": Decimal("200")}
    ]
    # Nested: [10,100] fully contains [20,30] — collapses.
    assert _merge_intervals([
        (Decimal("10"), Decimal("100")), (Decimal("20"), Decimal("30")),
    ]) == [{"min": Decimal("10"), "max": Decimal("100")}]
    # Far-apart trio → three entries.
    assert _merge_intervals([
        (Decimal("1"), Decimal("2")),
        (Decimal("10"), Decimal("20")),
        (Decimal("100"), Decimal("200")),
    ]) == [
        {"min": Decimal("1"),   "max": Decimal("2")},
        {"min": Decimal("10"),  "max": Decimal("20")},
        {"min": Decimal("100"), "max": Decimal("200")},
    ]
    # Out-of-order input is still sorted.
    assert _merge_intervals([
        (Decimal("5000"), Decimal("10000")),
        (Decimal("4000"), Decimal("4500")),
    ]) == [
        {"min": Decimal("4000"), "max": Decimal("4500")},
        {"min": Decimal("5000"), "max": Decimal("10000")},
    ]
