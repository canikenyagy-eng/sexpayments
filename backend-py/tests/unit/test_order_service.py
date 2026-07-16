import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from decimal import Decimal
from datetime import datetime
import uuid

from app.modules.orders.service import OrderService
from app.modules.orders.models import Order
from app.modules.merchants.models import Merchant
from app.modules.users.models import User
from app.modules.rates.models import RateConfig
from app.modules.requisites.models import Requisite
from app.modules.orders.schemas import MerchantPayinCreate
from app.modules.pooling.service import PoolingResult
from app.common.enums.payments import PaymentMethod, PaymentDirection
from app.common.enums.finances import Currency
from app.common.enums.orders import OrderStatus
from app.common.enums.receipts import ReceiptUploader
from app.core.exceptions import ValidationException, NotFoundException
from app.core.context import payin_snapshot_var

# Minimal valid PDF — confirm_order format-gates the uploaded file (ext + magic byte).
_VALID_PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\n%%EOF"


class AsyncContextManagerMock:
    async def __aenter__(self):
        return self
    async def __aexit__(self, exc_type, exc, tb):
        pass


@pytest.fixture
def mock_session():
    session = MagicMock()
    session.begin.return_value = AsyncContextManagerMock()
    session.begin_nested.return_value = AsyncContextManagerMock()
    session.flush = AsyncMock()
    session.execute = AsyncMock()
    session.get = AsyncMock()
    return session


@pytest.fixture
def service(mock_session):
    svc = OrderService(mock_session)
    svc.repository = AsyncMock()
    svc.audit_log = AsyncMock()
    # change_status row-locks the order + reads the committed status via
    # repository.lock_status; default to None so it falls back to the in-memory
    # order.status these mock tests set (real DB lock is exercised separately).
    svc.repository.lock_status = AsyncMock(return_value=None)
    return svc


@pytest.fixture
def mock_merchant():
    merchant = MagicMock(spec=Merchant)
    merchant.id = 1
    merchant.user_id = 10
    merchant.name = "Test Merchant"
    merchant.status = MagicMock()
    merchant.status.value = "enabled"
    merchant.order_ttl_seconds = 3600
    merchant.fees = {"card": 2.0}
    merchant.currency = Currency.RUB
    merchant.trader_groups = []
    merchant.rate_config_id = None
    # Default: no per-merchant premoderation override — confirm_order
    # falls back to the global PlatformSetting (which is False by default).
    merchant.receipt_premoderation_enabled = None
    merchant.notify_telegram_group_id = None
    return merchant


@pytest.fixture(autouse=True)
def reset_snapshot_var():
    """Reset the payin_snapshot context var before/after each test."""
    token = payin_snapshot_var.set(None)
    yield
    payin_snapshot_var.reset(token)


@pytest.mark.asyncio
@patch("app.modules.orders.service.PoolingService")
@patch("app.modules.orders.service.RateService")
async def test_create_payin_order(mock_rate_service_class, mock_pooling_service_class, service, mock_session, mock_merchant):
    mock_rate_service = MagicMock()
    mock_rate_config = MagicMock(spec=RateConfig)
    mock_rate_config.fiat_currency = Currency.RUB
    mock_rate_config.current_rate = 100.0
    mock_rate_config.id = 1
    mock_rate_config.name = "RUB rate"
    mock_rate_config.source = MagicMock()
    mock_rate_config.source.value = "bybit"
    mock_rate_config.is_active = True
    mock_rate_config.last_updated_at = None
    mock_rate_service.get_active_configs = AsyncMock(return_value=[mock_rate_config])
    mock_rate_service_class.return_value = mock_rate_service

    mock_requisite = MagicMock(spec=Requisite)
    mock_requisite.id = 50
    mock_requisite.trader_id = 5

    mock_pooling_service = MagicMock()
    mock_pooling_result = PoolingResult(
        selected=mock_requisite,
        candidates=[{"id": 50, "trader_id": 5}],
    )
    mock_pooling_service.select_requisite_with_diagnostics = AsyncMock(return_value=mock_pooling_result)
    mock_pooling_service_class.return_value = mock_pooling_service

    mock_order = MagicMock(spec=Order)
    mock_order.id = 100
    mock_order.uuid = uuid.uuid4()
    mock_order.direction = PaymentDirection.PAYIN
    mock_order.amount_usdt = Decimal("10.0")
    service.repository.create = AsyncMock(return_value=mock_order)
    service.repository.get_by_external_id_and_merchant = AsyncMock(return_value=None)
    service._reload_order = AsyncMock(return_value=mock_order)

    mock_trader = MagicMock()
    mock_trader.methods_config = {"card": {"fee": 10.0}}
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_trader
    mock_scalars = MagicMock()
    mock_scalars.unique.return_value = mock_scalars
    mock_scalars.all.return_value = []
    mock_result.scalars.return_value = mock_scalars
    mock_session.execute.return_value = mock_result

    data = MerchantPayinCreate(
        amount=1000.0,
        currency=Currency.RUB,
        payment_method=PaymentMethod.CARD,
        internalId="ext-123",
        issue_requisite_async=False
    )

    # Mock atomic limit-claim — отдельно покрыт в test_order_atomic_claim.py
    # (тут юнит на happy path create_payin_order, не на claim-логику).
    service._atomic_claim_requisite_capacity = AsyncMock(return_value=None)

    with patch("app.modules.finance.service.FinanceService") as mock_finance_cls:
        mock_finance_service = MagicMock()
        mock_finance_service.create_order = AsyncMock()
        mock_finance_cls.return_value = mock_finance_service
        result = await service.create_payin_order(mock_merchant, data)

    assert result == mock_order
    service.repository.create.assert_called_once()

    create_args = service.repository.create.call_args[0][0]
    assert create_args["merchant_id"] == 1
    assert create_args["amount"] == 1000.0
    assert create_args["currency"] == Currency.RUB
    assert create_args["payment_method"] == PaymentMethod.CARD
    assert create_args["external_id"] == "ext-123"
    assert create_args["amount_usdt"] == Decimal("10.0000")
    assert create_args["fee_usdt"] == Decimal("0.2000")
    assert create_args["profit_usdt"] == Decimal("9.8000")
    assert create_args["status"] == OrderStatus.PENDING
    assert create_args["requisite_id"] == 50
    assert create_args["trader_id"] == 5

    snap = payin_snapshot_var.get()
    assert snap is not None
    assert snap["result"]["success"] is True
    assert snap["result"]["selected_requisite_id"] == 50
    assert snap["request_data"]["amount"] == 1000.0
    assert snap["merchant_snapshot"]["id"] == 1


@pytest.mark.asyncio
@patch("app.modules.orders.service.PoolingService")
@patch("app.modules.orders.service.RateService")
async def test_create_payin_order_no_requisite_sets_snapshot(mock_rate_service_class, mock_pooling_service_class, service, mock_session, mock_merchant):
    """When no requisite is found, snapshot is still written with error info."""
    mock_rate_service = MagicMock()
    mock_rate_config = MagicMock(spec=RateConfig)
    mock_rate_config.fiat_currency = Currency.RUB
    mock_rate_config.current_rate = 100.0
    mock_rate_config.id = 1
    mock_rate_config.name = "RUB rate"
    mock_rate_config.source = MagicMock()
    mock_rate_config.source.value = "bybit"
    mock_rate_config.is_active = True
    mock_rate_config.last_updated_at = None
    mock_rate_service.get_active_configs = AsyncMock(return_value=[mock_rate_config])
    mock_rate_service_class.return_value = mock_rate_service

    mock_pooling_service = MagicMock()
    mock_pooling_result = PoolingResult(
        selected=None,
        candidates=[],
    )
    mock_pooling_service.select_requisite_with_diagnostics = AsyncMock(return_value=mock_pooling_result)
    mock_pooling_service_class.return_value = mock_pooling_service

    mock_scalars = MagicMock()
    mock_scalars.unique.return_value = mock_scalars
    mock_scalars.all.return_value = []
    mock_result = MagicMock()
    mock_result.scalars.return_value = mock_scalars
    mock_session.execute.return_value = mock_result

    data = MerchantPayinCreate(
        amount=1000.0,
        currency=Currency.RUB,
        payment_method=PaymentMethod.CARD,
        issue_requisite_async=False
    )

    with pytest.raises(NotFoundException):
        await service.create_payin_order(mock_merchant, data)

    snap = payin_snapshot_var.get()
    assert snap is not None
    assert snap["result"]["success"] is False
    assert "No available requisite" in snap["result"]["error"]
    # Excluded info should no longer be saved in snapshot
    # assert len(snap["excluded"]) == 1


@pytest.mark.asyncio
@patch("app.modules.orders.service.PoolingService")
@patch("app.modules.orders.service.redis_client")
async def test_assign_requisite_success(mock_redis, mock_pooling_service_class, service, mock_session):
    mock_order = MagicMock(spec=Order)
    mock_order.id = 100
    mock_order.status = OrderStatus.CREATED
    mock_order.uuid = uuid.uuid4()
    mock_order.payment_method = PaymentMethod.CARD
    mock_order.amount_usdt = Decimal("10.0")
    mock_order.direction = PaymentDirection.PAYIN

    service.repository.get = AsyncMock(return_value=mock_order)
    service.repository.update = AsyncMock(return_value=mock_order)

    mock_pooling_service = MagicMock()
    mock_requisite = MagicMock(spec=Requisite)
    mock_requisite.id = 50
    mock_requisite.trader_id = 5
    mock_pooling_service.select_requisite = AsyncMock(return_value=mock_requisite)
    mock_pooling_service_class.return_value = mock_pooling_service

    mock_redis.publish = AsyncMock()
    
    # Mock finance service
    with patch("app.modules.finance.service.FinanceService") as mock_finance_cls:
        mock_finance_service = MagicMock()
        mock_finance_service.create_order = AsyncMock()
        mock_finance_cls.return_value = mock_finance_service
        
        # Mock trader profile
        mock_trader = MagicMock()
        mock_trader.methods_config = {"card": {"fee": 10.0}}
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_trader
        mock_session.execute.return_value = mock_result
    
        await service.assign_requisite(100)

    service.repository.get.assert_called_once_with(100)
    mock_redis.publish.assert_called_once_with(f"order_updates:{100}", "assigned")


@pytest.mark.asyncio
async def test_cancel_order(service, mock_merchant):
    mock_order = MagicMock(spec=Order)
    mock_order.id = 100
    mock_order.status = OrderStatus.PENDING
    
    service.repository.get_by_uuid_and_merchant = AsyncMock(return_value=mock_order)
    service.repository.update = AsyncMock(return_value=mock_order)
    
    result = await service.cancel_order(mock_merchant, order_id="some-uuid", reason="Test cancel")
    
    assert result == mock_order
    service.repository.get_by_uuid_and_merchant.assert_called_once_with("some-uuid", mock_merchant.id)
    # First update writes the status transition; a second projects the Phase-2
    # financial snapshot. Assert the status write specifically.
    status_args = service.repository.update.call_args_list[0].args
    assert status_args[0] == 100
    assert status_args[1]["status"] == OrderStatus.CANCELED
    assert status_args[1]["rejection_reason"] == "Test cancel"
    
    service.audit_log.assert_called_once_with(
        action="cancel_order",
        entity_type="order",
        entity_id=100,
        user_id=10,
        new_values={"status": OrderStatus.CANCELED.value, "reason": "Test cancel"}
    )


@pytest.mark.asyncio
async def test_confirm_order_delegates_to_receipt_upload(service, mock_merchant):
    """confirm_order is a thin transport wrapper: resolve the order (by uuid)
    + read the bytes, then hand the whole lifecycle to ReceiptService.upload."""
    order = MagicMock(spec=Order)
    order.id = 100
    order.uuid = uuid.uuid4()
    service.repository.get_by_uuid_and_merchant = AsyncMock(return_value=order)

    att = AsyncMock()
    att.read = AsyncMock(return_value=_VALID_PDF)  # confirm_order now format-gates the file
    att.filename = "r.pdf"

    with patch("app.modules.receipts.service.ReceiptService") as RS:
        RS.return_value.upload = AsyncMock(return_value="UPDATED_ORDER")
        out = await service.confirm_order(
            mock_merchant, att, order_id="u",
            uploaded_by=ReceiptUploader.MERCHANT_DISPUTE_BOT, dispute_id=42,
        )

    assert out == "UPDATED_ORDER"
    kw = RS.return_value.upload.await_args.kwargs
    assert kw["order"] is order and kw["merchant"] is mock_merchant
    assert kw["content"] == _VALID_PDF and kw["filename"] == "r.pdf"
    assert kw["uploaded_by"] == ReceiptUploader.MERCHANT_DISPUTE_BOT and kw["dispute_id"] == 42


@pytest.mark.asyncio
async def test_confirm_order_resolves_by_external_id(service, mock_merchant):
    order = MagicMock(spec=Order)
    order.uuid = uuid.uuid4()
    service.repository.get_by_external_id_and_merchant = AsyncMock(return_value=order)
    att = AsyncMock()
    att.read = AsyncMock(return_value=_VALID_PDF)
    att.filename = "r.pdf"
    with patch("app.modules.receipts.service.ReceiptService") as RS:
        RS.return_value.upload = AsyncMock(return_value=order)
        await service.confirm_order(mock_merchant, att, external_id="ext-9")
    service.repository.get_by_external_id_and_merchant.assert_awaited_once()
    RS.return_value.upload.assert_awaited_once()


@pytest.mark.asyncio
async def test_confirm_order_not_found(service, mock_merchant):
    service.repository.get_by_uuid_and_merchant = AsyncMock(return_value=None)
    att = AsyncMock()
    att.read = AsyncMock(return_value=_VALID_PDF)  # valid file → the NotFound is about the order
    att.filename = "r.pdf"
    with pytest.raises(NotFoundException):
        await service.confirm_order(mock_merchant, att, order_id="missing")


@pytest.mark.asyncio
async def test_confirm_order_rejects_bad_file_format(service, mock_merchant):
    """A renamed/garbage file (extension says .png, bytes aren't an image) is
    rejected BEFORE any DB lookup or upload — closes the gap that confirm-transfer
    / dispute-bot / the merchant /receipt re-attach all share."""
    att = AsyncMock()
    att.read = AsyncMock(return_value=b"not a real image at all")
    att.filename = "proof.png"
    service.repository.get_by_uuid_and_merchant = AsyncMock()
    with patch("app.modules.receipts.service.ReceiptService") as RS:
        RS.return_value.upload = AsyncMock()
        with pytest.raises(ValidationException):
            await service.confirm_order(mock_merchant, att, order_id="u")
        RS.return_value.upload.assert_not_awaited()
    service.repository.get_by_uuid_and_merchant.assert_not_awaited()  # fail-fast, no DB hit


@pytest.mark.asyncio
async def test_confirm_order_requires_an_identifier(service, mock_merchant):
    att = AsyncMock()
    with pytest.raises(ValidationException):
        await service.confirm_order(mock_merchant, att)  # no order_id / external_id


# ────────────────────────────────────────────────────────────────
# fail_order — terminal transition driven by background tasks. Must
# release escrow back to trader and notify the merchant.
# ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fail_order_payin_releases_escrow_and_notifies(service, mock_session):
    mock_order = MagicMock(spec=Order)
    mock_order.id = 100
    mock_order.status = OrderStatus.PENDING
    mock_order.direction = PaymentDirection.PAYIN
    mock_order.trader_id = 5
    mock_order.merchant_id = 1

    service.repository.get = AsyncMock(return_value=mock_order)
    service.repository.update = AsyncMock(return_value=mock_order)

    trader = MagicMock(spec=User)
    trader.id = 5
    merchant = MagicMock(spec=Merchant)
    merchant.id = 1
    mock_session.get = AsyncMock(side_effect=lambda model, _id: trader if model is User else merchant)

    finance_service = MagicMock()
    finance_service.cancel_order = AsyncMock()

    with patch("app.modules.finance.service.FinanceService", return_value=finance_service), \
         patch("app.modules.orders.service.celery_app") as celery_mock:
        celery_mock.send_task = MagicMock()
        result = await service.fail_order(100, "payment timed out", trader=trader)

    assert result is mock_order
    # First update = status transition; a second projects the financial snapshot.
    status_args = service.repository.update.call_args_list[0].args
    assert status_args[0] == 100
    assert status_args[1]["status"] == OrderStatus.FAILED
    assert status_args[1]["rejection_reason"] == "payment timed out"
    assert "rejected_at" in status_args[1]

    finance_service.cancel_order.assert_awaited_once()
    cancel_kwargs = finance_service.cancel_order.await_args.kwargs
    assert cancel_kwargs["order"] is mock_order
    assert cancel_kwargs["trader"] is trader
    assert cancel_kwargs["merchant"] is merchant

    service.audit_log.assert_called_once()
    audit_kwargs = service.audit_log.call_args.kwargs
    assert audit_kwargs["action"] == "fail_order"
    assert audit_kwargs["new_values"]["reason"] == "payment timed out"
    assert audit_kwargs["new_values"]["status"] == OrderStatus.FAILED.value

    # Webhook notification queued via Celery
    celery_mock.send_task.assert_called_once()


@pytest.mark.asyncio
async def test_fail_order_not_found(service):
    service.repository.get = AsyncMock(return_value=None)

    with pytest.raises(NotFoundException, match="Order 999 not found"):
        await service.fail_order(999, "no")


@pytest.mark.asyncio
async def test_fail_order_terminal_state_rejected(service):
    """Order already in SUCCESS — fail_order must not run any escrow flow."""
    from app.core.exceptions import ConflictException

    mock_order = MagicMock(spec=Order)
    mock_order.id = 100
    mock_order.status = OrderStatus.SUCCESS
    service.repository.get = AsyncMock(return_value=mock_order)

    with pytest.raises(ConflictException):
        await service.fail_order(100, "stale")

    service.repository.update.assert_not_called()
    service.audit_log.assert_not_called()


@pytest.mark.asyncio
async def test_fail_order_wrong_trader_forbidden(service):
    """A trader cannot fail an order they aren't assigned to."""
    from app.core.exceptions import ForbiddenException

    mock_order = MagicMock(spec=Order)
    mock_order.id = 100
    mock_order.status = OrderStatus.PENDING
    mock_order.direction = PaymentDirection.PAYIN
    mock_order.trader_id = 5

    service.repository.get = AsyncMock(return_value=mock_order)

    intruder = MagicMock(spec=User)
    intruder.id = 999

    with pytest.raises(ForbiddenException):
        await service.fail_order(100, "no", trader=intruder)

    service.repository.update.assert_not_called()


@pytest.mark.asyncio
async def test_fail_order_swallows_insufficient_funds(service, mock_session):
    """If escrow has already been drained, log and continue rather than abort."""
    mock_order = MagicMock(spec=Order)
    mock_order.id = 100
    mock_order.status = OrderStatus.PENDING
    mock_order.direction = PaymentDirection.PAYIN
    mock_order.trader_id = 5
    mock_order.merchant_id = 1

    service.repository.get = AsyncMock(return_value=mock_order)
    service.repository.update = AsyncMock(return_value=mock_order)

    trader = MagicMock(spec=User); trader.id = 5
    merchant = MagicMock(spec=Merchant); merchant.id = 1
    mock_session.get = AsyncMock(side_effect=lambda model, _id: trader if model is User else merchant)

    finance_service = MagicMock()
    finance_service.cancel_order = AsyncMock(side_effect=ValidationException("Insufficient funds"))

    with patch("app.modules.finance.service.FinanceService", return_value=finance_service), \
         patch("app.modules.orders.service.celery_app") as celery_mock:
        celery_mock.send_task = MagicMock()
        result = await service.fail_order(100, "timeout", trader=trader)

    assert result is mock_order
    # Escrow release raised "Insufficient funds" → swallowed + logged; the
    # fail_order audit is still recorded and the call doesn't abort.
    assert service.audit_log.await_count == 1
    assert finance_service.cancel_order.await_count == 1


# ────────────────────────────────────────────────────────────────
# list_admin_orders — pure repo pass-through with kwargs forwarding.
# ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_admin_orders_forwards_filters_and_pagination(service):
    """Admin role-method translates its inputs into the generic list_orders
    filters: status→statuses, id_search→search(scope=admin), search→login_search."""
    expected = [MagicMock(spec=Order)]
    service.repository.list_orders = AsyncMock(return_value=expected)

    result = await service.list_admin_orders(
        merchant_id=1, trader_id=2, status="success",
        search="abc", id_search="ord_",
        trader_login="t", merchant_login="m",
        payment_method="card", amount_from=10.0, amount_to=100.0,
        skip=20, limit=50,
    )

    assert result is expected
    service.repository.list_orders.assert_awaited_once_with(
        merchant_ids=1, trader_id=2, statuses=["success"],
        payment_method="card", amount_from=10.0, amount_to=100.0,
        search="ord_", search_scope="admin",
        login_search="abc", trader_login="t", merchant_login="m",
        skip=20, limit=50,
    )


@pytest.mark.asyncio
async def test_list_admin_orders_default_pagination(service):
    service.repository.list_orders = AsyncMock(return_value=[])

    await service.list_admin_orders()

    kwargs = service.repository.list_orders.await_args.kwargs
    assert kwargs["skip"] == 0
    assert kwargs["limit"] == 100
    assert kwargs["search_scope"] == "admin"


# ────────────────────────────────────────────────────────────────
# get_orders_for_merchant_in_period — used by callback re-emit.
# ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_orders_for_merchant_in_period_passthrough(service):
    expected = [MagicMock(spec=Order), MagicMock(spec=Order)]
    service.repository.list_orders = AsyncMock(return_value=expected)

    start = datetime(2026, 5, 1)
    end = datetime(2026, 5, 8)

    result = await service.get_orders_for_merchant_in_period(7, start, end)

    assert result is expected
    # order=None preserves the old "no ORDER BY / no limit" period query.
    service.repository.list_orders.assert_awaited_once_with(
        merchant_ids=7, created_from=start, created_to=end, order=None,
    )


@pytest.mark.asyncio
async def test_get_orders_for_merchant_in_period_optional_end(service):
    service.repository.list_orders = AsyncMock(return_value=[])

    start = datetime(2026, 5, 1)
    await service.get_orders_for_merchant_in_period(7, start)

    kwargs = service.repository.list_orders.await_args.kwargs
    assert kwargs["created_to"] is None


# ────────────────────────────────────────────────────────────────
# Cascade integration regression — _cascade_enabled defensive
# checks + verifying that the cascade hot path stays a no-op for
# merchants with cascade_mode='off' (the default for legacy rows).
# ────────────────────────────────────────────────────────────────


def test_cascade_enabled_defensive_against_legacy_and_mocks():
    """``_cascade_enabled`` is the on/off gate read from the merchant row.
    Old DB rows / Mock-ed merchants without ``cascade_mode`` must read as
    "off" — otherwise feature-flagging breaks for migrated data."""
    from app.modules.orders.service import _cascade_enabled
    from app.common.enums.cascading import CascadeMode

    # 1. Merchant with no cascade_mode attribute at all (legacy mock).
    merch_legacy = MagicMock(spec=[])  # spec=[] — strict, no attrs
    assert _cascade_enabled(merch_legacy) is False

    # 2. cascade_mode = None (post-migration row before backfill).
    merch_none = MagicMock()
    merch_none.cascade_mode = None
    assert _cascade_enabled(merch_none) is False

    # 3. cascade_mode = enum OFF.
    merch_off = MagicMock()
    merch_off.cascade_mode = CascadeMode.OFF
    assert _cascade_enabled(merch_off) is False

    # 4. cascade_mode = raw string 'off' (DB driver returns plain str sometimes).
    merch_off_str = MagicMock()
    merch_off_str.cascade_mode = "off"
    assert _cascade_enabled(merch_off_str) is False

    # 5. Both cascade flavours flip the gate ON.
    merch_grouped = MagicMock()
    merch_grouped.cascade_mode = CascadeMode.GROUPED
    assert _cascade_enabled(merch_grouped) is True

    merch_pooled = MagicMock()
    merch_pooled.cascade_mode = CascadeMode.POOLED
    assert _cascade_enabled(merch_pooled) is True

    # 6. Raw string variants — same.
    merch_str_grouped = MagicMock()
    merch_str_grouped.cascade_mode = "grouped"
    assert _cascade_enabled(merch_str_grouped) is True


@pytest.mark.asyncio
@patch("app.modules.orders.service.PoolingService")
@patch("app.modules.orders.service.RateService")
async def test_create_payin_order_with_cascade_off_does_not_call_cascading(
    mock_rate_service_class,
    mock_pooling_service_class,
    service,
    mock_session,
    mock_merchant,
):
    """Regression: a merchant with ``cascade_mode='off'`` (the default for
    every existing row after migration 029) must NOT invoke ``CascadingService``
    even when the local pooler returns no requisite. This guarantees the
    cascade feature is fully feature-gated and the trader-only flow keeps
    working as before."""
    from app.common.enums.cascading import CascadeMode

    # Default-installed merchant — explicit OFF.
    mock_merchant.cascade_mode = CascadeMode.OFF

    # Standard rate service stub.
    mock_rate_service = MagicMock()
    mock_rate_config = MagicMock(spec=RateConfig)
    mock_rate_config.fiat_currency = Currency.RUB
    mock_rate_config.current_rate = 100.0
    mock_rate_config.id = 1
    mock_rate_config.name = "RUB"
    mock_rate_config.source = MagicMock(value="bybit")
    mock_rate_config.is_active = True
    mock_rate_config.last_updated_at = None
    mock_rate_service.get_active_configs = AsyncMock(return_value=[mock_rate_config])
    mock_rate_service_class.return_value = mock_rate_service

    # Local pool returns NO requisite → cascade gate would fire if it weren't off.
    mock_pooling_service = MagicMock()
    mock_pooling_service.select_requisite_with_diagnostics = AsyncMock(
        return_value=PoolingResult(selected=None, candidates=[])
    )
    mock_pooling_service_class.return_value = mock_pooling_service

    # Trader snapshot helper used by create_payin_order.
    mock_scalars = MagicMock()
    mock_scalars.unique.return_value = mock_scalars
    mock_scalars.all.return_value = []
    mock_result = MagicMock()
    mock_result.scalars.return_value = mock_scalars
    mock_session.execute.return_value = mock_result

    data = MerchantPayinCreate(
        amount=1000.0,
        currency=Currency.RUB,
        payment_method=PaymentMethod.SBP,
        issue_requisite_async=False,
    )

    # The crucial assertion is via patch: CascadingService must not be
    # imported/instantiated in the hot path when cascade is off.
    cascading_cls = MagicMock()
    cascading_inst = MagicMock()
    cascading_inst.try_cascade = AsyncMock()
    cascading_cls.return_value = cascading_inst

    with patch("app.modules.cascading.service.CascadingService", cascading_cls):
        with pytest.raises(NotFoundException):
            await service.create_payin_order(mock_merchant, data)

    cascading_cls.assert_not_called()
    cascading_inst.try_cascade.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_order_swallows_cascade_broker_errors(service, mock_merchant):
    """A merchant cancelling a non-cascade order must NOT see a 5xx if the
    Celery broker is unreachable when the cascade-cancel notification is
    enqueued. The dispatch is best-effort plumbing — the cancel itself
    has already committed."""
    mock_order = MagicMock(spec=Order)
    mock_order.id = 100
    mock_order.status = OrderStatus.PENDING

    service.repository.get_by_uuid_and_merchant = AsyncMock(return_value=mock_order)
    service.repository.update = AsyncMock(return_value=mock_order)

    # First send_task is the merchant callback (works), second is cascade
    # cancel (raises). Both are post-commit, but only the cascade one is
    # protected by try/except.
    def _send_task_side_effect(name, *args, **kwargs):
        if "cascade" in name:
            raise RuntimeError("Connection refused (broker down)")
        return None

    with patch("app.modules.orders.service.celery_app") as celery_mock:
        celery_mock.send_task = MagicMock(side_effect=_send_task_side_effect)

        # Must NOT raise — the cancel order returns normally despite the
        # broker error on the cascade dispatch.
        result = await service.cancel_order(
            mock_merchant, order_id="some-uuid", reason="Test cancel"
        )
        assert result is mock_order

    # Both send_task attempts were made.
    cascade_calls = [
        c for c in celery_mock.send_task.call_args_list
        if "cascade" in c.args[0]
    ]
    assert len(cascade_calls) == 1, "cascade cancel notification should be dispatched"


@pytest.mark.asyncio
async def test_reload_order_single_joined_query(service, mock_session):
    """Perf guard: _reload_order pulls requisite + payment_option in ONE joined
    query (not a per-relationship selectin fan-out), so building the response
    doesn't cost ~6 round-trips. Result/behaviour are unchanged."""
    order_obj = MagicMock(spec=Order, id=42)
    result = MagicMock()
    result.scalars.return_value.first.return_value = order_obj
    mock_session.execute.return_value = result

    out = await service._reload_order(42)

    assert out is order_obj
    assert mock_session.execute.call_count == 1
    sql = str(mock_session.execute.call_args[0][0])
    # joinedload renders the relationships as JOINs in the single statement;
    # selectinload would leave the main query join-free and fan out instead.
    assert "JOIN requisites" in sql
    assert "JOIN payment_options" in sql


@pytest.mark.asyncio
async def test_calculate_trader_fee_loads_only_method_configs(service, mock_session):
    """Perf guard: the fee calc loads ONLY Trader.method_configs (selectinload) and
    raiseloads the rest, so Trader.groups / Trader.merchants don't fan out two extra
    selectin queries per payin. Result/behaviour unchanged."""
    mc = MagicMock()
    mc.payment_method = MagicMock(value="sbp")
    mc.fee = Decimal("1.0")
    trader = MagicMock()
    trader.method_configs = [mc]
    trader.achievement_bonus_percent = Decimal("0")
    result = MagicMock()
    result.scalar_one_or_none.return_value = trader
    mock_session.execute.return_value = result

    fee = await service._calculate_trader_fee(10, "sbp", Decimal("100"))
    assert fee == Decimal("1.0000")  # 100 * 1%

    # the select must carry loader options (not a bare select(Trader) that
    # would fan out groups/merchants/method_configs via lazy="selectin").
    stmt = mock_session.execute.call_args[0][0]
    assert len(stmt._with_options) == 2


@pytest.mark.asyncio
async def test_calculate_trader_fee_includes_achievement_bonus(service, mock_session):
    """The trader's materialized achievements bonus is added (percentage points)
    to the method fee when stamping trader_fee_usdt — same additive shape as
    PrimeTime."""
    mc = MagicMock()
    mc.payment_method = MagicMock(value="sbp")
    mc.fee = Decimal("1.0")
    trader = MagicMock()
    trader.method_configs = [mc]
    trader.achievement_bonus_percent = Decimal("0.5")
    result = MagicMock()
    result.scalar_one_or_none.return_value = trader
    mock_session.execute.return_value = result

    fee = await service._calculate_trader_fee(10, "sbp", Decimal("100"))
    assert fee == Decimal("1.5000")  # 100 * (1% + 0.5% bonus)


# ────────────────────────────────────────────────────────────────
# admin_update_order — open-dispute guard. An order with an OPEN
# dispute owns its escrow lifecycle via resolve/reject; admin must
# not force-flip its status here (would double-release escrow).
# ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_admin_update_order_blocked_when_open_dispute(service, mock_session):
    """DISPUTED order with an OPEN dispute → status override raises
    ConflictException instead of releasing escrow a second time."""
    from app.core.exceptions import ConflictException
    from app.modules.orders.schemas import AdminOrderUpdate

    mock_order = MagicMock(spec=Order)
    mock_order.id = 100
    mock_order.status = OrderStatus.DISPUTED
    service.repository.get = AsyncMock(return_value=mock_order)

    # select(Dispute.id … status=OPEN).limit(1) → returns a dispute id.
    dispute_result = MagicMock()
    dispute_result.scalar_one_or_none.return_value = 777
    mock_session.execute = AsyncMock(return_value=dispute_result)

    data = AdminOrderUpdate(status=OrderStatus.FAILED, reason="x")

    with pytest.raises(ConflictException, match="open dispute"):
        await service.admin_update_order(100, data, admin_user_id=1)

    # Guard fired before any status write / finance flow.
    service.repository.update.assert_not_called()


@pytest.mark.asyncio
async def test_admin_update_order_allows_when_no_open_dispute(service, mock_session):
    """No OPEN dispute → guard passes; the status flow proceeds (we assert it
    gets past the guard by reaching repository.update)."""
    from app.modules.orders.schemas import AdminOrderUpdate

    mock_order = MagicMock(spec=Order)
    mock_order.id = 100
    mock_order.status = OrderStatus.PENDING
    mock_order.direction = PaymentDirection.PAYIN
    mock_order.trader_id = 5
    mock_order.merchant_id = 1
    mock_order.amount_usdt = Decimal("10")
    service.repository.get = AsyncMock(return_value=mock_order)
    service.repository.update = AsyncMock(return_value=mock_order)

    # Dispute lookup → none. All other execute() calls also resolve to a mock
    # whose scalar_one_or_none() is None (harmless for the cancel path).
    no_dispute = MagicMock()
    no_dispute.scalar_one_or_none.return_value = None
    mock_session.execute = AsyncMock(return_value=no_dispute)
    mock_session.get = AsyncMock(return_value=MagicMock())

    data = AdminOrderUpdate(status=OrderStatus.CANCELED, reason="x")

    with patch("app.modules.finance.service.FinanceService") as fin_cls:
        fin = MagicMock()
        fin.cancel_order = AsyncMock()
        fin_cls.return_value = fin
        await service.admin_update_order(100, data, admin_user_id=1)

    # Passed the guard and reached the status write.
    service.repository.update.assert_called()


@pytest.mark.asyncio
async def test_admin_update_order_allows_refunded(service, mock_session):
    """Admin may set REFUNDED — the full-unwind ("as if the order never
    happened") path. SUCCESS→REFUNDED is terminal→terminal, so it must pass the
    terminal-status guard, reverse the prior settlement (reconcile_for_dispute)
    and then release the collateral back to the trader (cancel_order)."""
    from app.modules.orders.schemas import AdminOrderUpdate

    mock_order = MagicMock(spec=Order)
    mock_order.id = 100
    mock_order.status = OrderStatus.SUCCESS
    mock_order.direction = PaymentDirection.PAYIN
    mock_order.trader_id = 5
    mock_order.merchant_id = 1
    mock_order.amount_usdt = Decimal("10")
    service.repository.get = AsyncMock(return_value=mock_order)
    service.repository.update = AsyncMock(return_value=mock_order)

    no_dispute = MagicMock()
    no_dispute.scalar_one_or_none.return_value = None
    mock_session.execute = AsyncMock(return_value=no_dispute)
    mock_session.get = AsyncMock(return_value=MagicMock())

    data = AdminOrderUpdate(status=OrderStatus.REFUNDED, reason="admin refund")

    with patch("app.modules.finance.service.FinanceService") as fin_cls:
        fin = MagicMock()
        fin.reconcile_for_dispute = AsyncMock()
        fin.cancel_order = AsyncMock()
        fin_cls.return_value = fin
        await service.admin_update_order(100, data, admin_user_id=1)

    # Got past the terminal guard and ran the unwind: reverse settlement first,
    # then release the collateral — exactly like the other terminal overrides.
    fin.reconcile_for_dispute.assert_awaited_once()
    fin.cancel_order.assert_awaited_once()
    service.repository.update.assert_called()


# ── _resolve_trader_fee: cascade stamps the PROVIDER fee, not the virtual
#    trader's configured fee. This makes provider.fees the single driver of
#    cascade economics (ledger + dashboard profit == merchant_fee − provider_fee).


@pytest.mark.asyncio
async def test_resolve_trader_fee_cascade_uses_provider_fee(service):
    """For a cascade-routed order the platform's real cost is the provider's
    fee, so trader_fee_usdt must equal cascade_result.provider_fee_usdt and the
    virtual-trader config lookup (_calculate_trader_fee) must NOT run."""
    service._calculate_trader_fee = AsyncMock(
        side_effect=AssertionError("must not consult virtual-trader config for cascade")
    )
    found_requisite = MagicMock(spec=Requisite, trader_id=999)
    cascade_result = MagicMock(success=True, provider_fee_usdt=Decimal("3.5000"))

    fee = await service._resolve_trader_fee(
        found_requisite=found_requisite,
        cascade_result=cascade_result,
        payment_method="card",
        amount_usdt=Decimal("100"),
    )
    assert fee == Decimal("3.5000")
    service._calculate_trader_fee.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_trader_fee_local_uses_trader_config(service):
    """Local (non-cascade) orders keep using the trader's own configured fee."""
    service._calculate_trader_fee = AsyncMock(return_value=Decimal("1.2300"))
    found_requisite = MagicMock(spec=Requisite, trader_id=42)

    fee = await service._resolve_trader_fee(
        found_requisite=found_requisite,
        cascade_result=None,
        payment_method="sbp",
        amount_usdt=Decimal("100"),
    )
    assert fee == Decimal("1.2300")
    service._calculate_trader_fee.assert_awaited_once_with(42, "sbp", Decimal("100"))


@pytest.mark.asyncio
async def test_resolve_trader_fee_failed_cascade_falls_back_to_local(service):
    """A cascade_result that did NOT win must not be treated as cascade-routed —
    the requisite came from local pooling, so use the trader's configured fee."""
    service._calculate_trader_fee = AsyncMock(return_value=Decimal("2.0000"))
    found_requisite = MagicMock(spec=Requisite, trader_id=7)
    cascade_result = MagicMock(success=False, provider_fee_usdt=Decimal("9.9999"))

    fee = await service._resolve_trader_fee(
        found_requisite=found_requisite,
        cascade_result=cascade_result,
        payment_method="card",
        amount_usdt=Decimal("100"),
    )
    assert fee == Decimal("2.0000")
    service._calculate_trader_fee.assert_awaited_once()


# ── list_orders_for_export: spans ALL owner terminals, passes the period ──


@pytest.mark.asyncio
async def test_list_orders_for_export_resolves_terminals_and_period(service, mock_session):
    """Resolves every terminal owned by the user, then hands the date window
    to the repo (no status/method filter — period-only export)."""
    from datetime import datetime, timezone

    scalars = MagicMock()
    scalars.all.return_value = [11, 22]            # two terminals owned by the user
    res = MagicMock()
    res.scalars.return_value = scalars
    mock_session.execute = AsyncMock(return_value=res)
    service.repository.list_orders = AsyncMock(return_value=["o1", "o2"])

    df = datetime(2026, 6, 1, tzinfo=timezone.utc)
    dt = datetime(2026, 6, 30, tzinfo=timezone.utc)
    out = await service.list_orders_for_export(99, date_from=df, date_to=dt)

    assert out == ["o1", "o2"]
    # chronological export across all terminals (order="asc"), capped.
    service.repository.list_orders.assert_awaited_once_with(
        merchant_ids=[11, 22], created_from=df, created_to=dt,
        order="asc", limit=100_000,
    )


@pytest.mark.asyncio
async def test_list_orders_for_export_no_terminals_returns_empty(service, mock_session):
    """User owns no terminals → empty, repo never queried."""
    from datetime import datetime, timezone

    scalars = MagicMock()
    scalars.all.return_value = []
    res = MagicMock()
    res.scalars.return_value = scalars
    mock_session.execute = AsyncMock(return_value=res)
    service.repository.list_orders = AsyncMock()

    out = await service.list_orders_for_export(
        99, date_from=datetime(2026, 6, 1, tzinfo=timezone.utc),
        date_to=datetime(2026, 6, 30, tzinfo=timezone.utc),
    )
    assert out == []
    service.repository.list_orders.assert_not_called()




# ── complete_order idempotency: the double-confirm-race money-doubling fix ──

@pytest.mark.asyncio
async def test_complete_order_locks_row_and_settles_completable(service):
    """complete_order loads the order FOR UPDATE (the idempotency lock) and
    settles a completable order via change_status(SUCCESS)."""
    order = MagicMock(spec=Order)
    order.id = 100
    order.trader_id = 5
    order.status = OrderStatus.RECEIPT_UPLOADED
    service.repository.get_for_update = AsyncMock(return_value=order)
    settled = MagicMock(spec=Order)
    service.change_status = AsyncMock(return_value=settled)
    service._emit_selector_feedback = MagicMock()

    trader = MagicMock()
    trader.id = 5
    result = await service.complete_order(trader=trader, order_id=100)

    # Row-locked load (not a plain get) → serialises concurrent confirms.
    service.repository.get_for_update.assert_awaited_once_with(100)
    service.change_status.assert_awaited_once()
    assert service.change_status.call_args.args[1] == OrderStatus.SUCCESS
    assert result is settled


@pytest.mark.asyncio
async def test_complete_order_idempotent_when_already_success(service):
    """Second confirm for an already-SUCCESS order (cabinet + trader-bot button,
    double-click, or a retried request) is a NO-OP: returns the order, never
    re-runs settlement — the fix for double-debiting the trader / double-crediting
    the merchant."""
    order = MagicMock(spec=Order)
    order.id = 100
    order.trader_id = 5
    order.status = OrderStatus.SUCCESS
    service.repository.get_for_update = AsyncMock(return_value=order)
    service.change_status = AsyncMock()  # must NOT run a second settlement

    trader = MagicMock()
    trader.id = 5
    result = await service.complete_order(trader=trader, order_id=100)

    assert result is order
    service.change_status.assert_not_called()
    service.repository.get_for_update.assert_awaited_once_with(100)


@pytest.mark.asyncio
async def test_change_status_uses_locked_committed_status_not_snapshot(service):
    """The money funnel reads the AUTHORITATIVE status via the row lock, not the
    caller's (possibly stale) order.status. If a concurrent transition already
    settled the order (committed status == SUCCESS), a second →SUCCESS is a
    no-op — no second settlement. This closes the systemic double-settle on the
    dispute / settle-failed / admin / fail paths."""
    order = MagicMock(spec=Order)
    order.id = 100
    order.status = OrderStatus.RECEIPT_UPLOADED  # caller's STALE snapshot
    service.repository.lock_status = AsyncMock(return_value=OrderStatus.SUCCESS)

    result = await service.change_status(order, OrderStatus.SUCCESS)

    service.repository.lock_status.assert_awaited_once_with(100)
    assert result is order
    service.repository.update.assert_not_called()  # no transition / no money moved


@pytest.mark.asyncio
async def test_change_amount_rescales_trader_fee(service, mock_session, mock_merchant):
    """Changing an order's amount rescales trader_fee_usdt proportionally so a
    later re-settlement (dispute round-trip) pays the reward for the NEW amount,
    not the stale original (audit #19)."""
    order = MagicMock(spec=Order)
    order.id = 100
    order.amount = Decimal("1000")
    order.amount_usdt = Decimal("10")
    order.exchange_rate = Decimal("100")
    order.trader_fee_usdt = Decimal("0.20")   # reward for amount_usdt 10
    order.merchant_id = 1
    order.payment_method = PaymentMethod.CARD
    order.trader_id = 5
    order.status = OrderStatus.DISPUTED
    order.financials = {}
    order.teamlead_reward_usdt = Decimal("0")

    service.repository.update = AsyncMock(return_value=order)
    service._financial_snapshot = MagicMock(return_value={})
    trader = MagicMock(spec=User); trader.id = 5
    mock_session.get = AsyncMock(side_effect=lambda model, _id: mock_merchant if model is Merchant else trader)

    with patch("app.modules.finance.service.FinanceService") as fin_cls, \
         patch("app.modules.orders.service.celery_app"):
        fin = MagicMock(); fin.recalculate_order = AsyncMock(); fin_cls.return_value = fin
        await service.change_amount(order, Decimal("2000"))  # ×2

    fields = service.repository.update.call_args_list[0].args[1]
    assert fields["trader_fee_usdt"] == Decimal("0.4000")  # 0.20 × (20/10)
