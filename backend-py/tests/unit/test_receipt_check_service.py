"""Unit tests for ReceiptCheckService.

Strategy: we mock the session, both repositories, the active provider, the
provider client (so no network), and the FinanceService. Each test pins one
specific decision in the orchestration (no provider, cached file, refundable
error, etc.) and asserts the resulting `ReceiptCheck` row + ledger calls.
"""
from __future__ import annotations

import os
import tempfile
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.common.enums.receipt_checks import (
    ReceiptCheckProviderAdapter,
    ReceiptCheckStatus,
    ReceiptCheckTrigger,
)
from app.common.enums.users import UserRole
from app.core.exceptions import (
    ConflictException,
    NotFoundException,
    ValidationException,
)
from app.modules.orders.models import Order
# Importing Requisite + PaymentOption registers their mappers so the
# Order.relationship('Requisite') back-ref does not fail mapper init when
# this test file runs in isolation. Same pattern as test_finance_service.py.
from app.modules.requisites.models import Requisite  # noqa: F401
from app.modules.payments.models import PaymentOption  # noqa: F401
from app.modules.receipt_checks.models import ReceiptCheck, ReceiptCheckProvider
from app.modules.receipt_checks.schemas import (
    ProviderCheckResult,
    ProviderCreate,
    ProviderUpdate,
    ReceiptCheckVerdictItem,
)
from app.modules.receipt_checks.service import ReceiptCheckService, _mask_api_key
from app.modules.users.models import User


# ── Fixtures ────────────────────────────────────────────────────────────


class AsyncContextManagerMock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        pass


@pytest.fixture
def mock_session():
    s = MagicMock()
    s.begin.return_value = AsyncContextManagerMock()
    s.begin_nested.return_value = AsyncContextManagerMock()
    s.flush = AsyncMock()
    s.refresh = AsyncMock()
    s.execute = AsyncMock()
    return s


@pytest.fixture
def service(mock_session):
    svc = ReceiptCheckService(mock_session)
    svc.providers = AsyncMock()
    svc.checks = AsyncMock()
    svc.audit_log = AsyncMock()
    return svc


@pytest.fixture
def pdf_file(tmp_path):
    """Returns the path to a tiny on-disk PDF so checksum logic can run."""
    p = tmp_path / "receipt.pdf"
    p.write_bytes(b"%PDF-1.4\n%fake bytes\n")
    return str(p)


@pytest.fixture
def order(pdf_file):
    o = MagicMock(spec=Order)
    o.id = 42
    o.uuid = "uuid-42"
    o.receipt_file = pdf_file
    return o


@pytest.fixture
def trader_user():
    u = MagicMock(spec=User)
    u.id = 7
    u.role = UserRole.TRADER
    return u


@pytest.fixture
def active_provider():
    p = MagicMock(spec=ReceiptCheckProvider)
    p.id = 1
    p.code = "trexo"
    p.name = "TREXO"
    p.adapter_type = ReceiptCheckProviderAdapter.TREXO.value
    p.is_active = True
    p.base_url = "https://api.trexo.example"
    p.api_key_encrypted = "encrypted::"
    p.api_key_tail = "1234"
    p.price_usdt = Decimal("0.50")
    p.request_timeout_ms = 90000
    p.settings = {}
    return p


def _make_check_row(**overrides) -> MagicMock:
    """Pretend the repository's `create` returned a ReceiptCheck row."""
    base = dict(
        id=999,
        order_id=42,
        provider_id=1,
        trader_user_id=7,
        trigger=ReceiptCheckTrigger.MANUAL.value,
        status=ReceiptCheckStatus.PENDING,
        file_path="",
        file_sha256="",
        is_clean=None,
        verdict=None,
        parsed_data=None,
        provider_check_id=None,
        provider_tx_id=None,
        raw_response=None,
        price_usdt=Decimal("0.50"),
        charged=False,
        refunded=False,
        error_code=None,
        error_message=None,
        finished_at=None,
    )
    base.update(overrides)
    row = MagicMock()
    for k, v in base.items():
        setattr(row, k, v)
    return row


# ── No provider configured ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_check_raises_when_no_active_provider(service, order, trader_user):
    service.providers.list_active = AsyncMock(return_value=[])

    with pytest.raises(ConflictException):
        await service.run_check_for_order(
            order=order, trader_user=trader_user, trigger=ReceiptCheckTrigger.MANUAL
        )


# ── Missing/invalid receipt ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_check_no_receipt_file_field(service, trader_user, active_provider):
    o = MagicMock(spec=Order)
    o.id = 1
    o.receipt_file = None
    service.providers.list_active = AsyncMock(return_value=[active_provider])

    with pytest.raises(ValidationException):
        await service.run_check_for_order(
            order=o, trader_user=trader_user, trigger=ReceiptCheckTrigger.MANUAL
        )


@pytest.mark.asyncio
async def test_run_check_file_missing_on_disk(service, trader_user, active_provider):
    o = MagicMock(spec=Order)
    o.id = 1
    o.receipt_file = "/nonexistent/path/receipt.pdf"
    service.providers.list_active = AsyncMock(return_value=[active_provider])

    with pytest.raises(ValidationException):
        await service.run_check_for_order(
            order=o, trader_user=trader_user, trigger=ReceiptCheckTrigger.MANUAL
        )


# ── Dedup: same file already verified → CACHED, no charge ───────────────


@pytest.mark.asyncio
async def test_run_check_returns_cached_for_already_verified_file(
    service, order, trader_user, active_provider
):
    prior = _make_check_row(
        status=ReceiptCheckStatus.SUCCESS,
        is_clean=True,
        verdict=[],
        parsed_data={"sum": "1000"},
        provider_check_id="111",
        provider_tx_id="api:tx:1",
    )
    cached_row = _make_check_row(
        status=ReceiptCheckStatus.CACHED,
        is_clean=True,
        price_usdt=Decimal("0"),
        charged=False,
    )

    service.providers.list_active = AsyncMock(return_value=[active_provider])
    service.checks.find_reusable_for_file = AsyncMock(return_value=prior)
    service.checks.create = AsyncMock(return_value=cached_row)

    result = await service.run_check_for_order(
        order=order, trader_user=trader_user, trigger=ReceiptCheckTrigger.MANUAL
    )

    assert result is cached_row
    # We MUST NOT have created a charging row + called the provider client.
    assert service.checks.create.call_count == 1
    payload = service.checks.create.call_args[0][0]
    assert payload["status"] == ReceiptCheckStatus.CACHED
    assert payload["price_usdt"] == Decimal("0")
    assert payload["charged"] is False


# ── Non-PDF file → marked FAILED locally without contacting provider ────


@pytest.mark.asyncio
async def test_run_check_rejects_non_pdf_locally(
    service, trader_user, active_provider, tmp_path
):
    jpg = tmp_path / "receipt.jpg"
    jpg.write_bytes(b"\xff\xd8\xff")  # JPEG magic bytes — but we only care about extension
    o = MagicMock(spec=Order)
    o.id = 99
    o.receipt_file = str(jpg)

    failed_row = _make_check_row(
        status=ReceiptCheckStatus.FAILED,
        error_code="unsupported_format",
        price_usdt=Decimal("0"),
    )
    service.providers.list_active = AsyncMock(return_value=[active_provider])
    service.checks.find_reusable_for_file = AsyncMock(return_value=None)
    service.checks.create = AsyncMock(return_value=failed_row)

    result = await service.run_check_for_order(
        order=o, trader_user=trader_user, trigger=ReceiptCheckTrigger.AUTO
    )

    assert result.error_code == "unsupported_format"
    # No client construction — assert the create() call payload didn't set "charged" true
    payload = service.checks.create.call_args[0][0]
    assert payload["charged"] is False
    assert payload["price_usdt"] == Decimal("0")


# ── Success path: charge, success row, NO refund ────────────────────────


@pytest.mark.asyncio
async def test_run_check_success_charges_and_records_clean_verdict(
    service, order, trader_user, active_provider
):
    pending = _make_check_row(status=ReceiptCheckStatus.PENDING)
    service.providers.list_active = AsyncMock(return_value=[active_provider])
    service.checks.find_reusable_for_file = AsyncMock(return_value=None)
    service.checks.create = AsyncMock(return_value=pending)

    # Patch the FinanceService used inside the service: charge_trader / refund_trader
    # call `_charge_trader` / `_refund_trader` which both build a FinanceService.
    with patch("app.modules.receipt_checks.service.FinanceService") as FS, \
         patch("app.modules.receipt_checks.service.build_client_for_provider") as build_client:
        FS.return_value = MagicMock()
        client = MagicMock()
        client.check_file = AsyncMock(
            return_value=ProviderCheckResult(
                is_clean=True,
                verdict=[ReceiptCheckVerdictItem(type="OK")],
                parsed_data={"sum": "1000"},
                provider_check_id="123",
                provider_tx_id="api:tx:1",
                raw_response={"is_clean": True},
                refundable=False,
            )
        )
        build_client.return_value = client

        # Charge passes
        service._charge_trader = AsyncMock()
        service._refund_trader = AsyncMock()

        result = await service.run_check_for_order(
            order=order, trader_user=trader_user, trigger=ReceiptCheckTrigger.MANUAL
        )

    service._charge_trader.assert_awaited_once()
    service._refund_trader.assert_not_called()
    assert result.status == ReceiptCheckStatus.SUCCESS
    assert result.is_clean is True
    assert result.error_code is None
    assert result.charged is True
    assert result.refunded is False


# ── Refundable provider error → charge then refund ──────────────────────


@pytest.mark.asyncio
async def test_run_check_refundable_error_triggers_refund(
    service, order, trader_user, active_provider
):
    pending = _make_check_row(status=ReceiptCheckStatus.PENDING)
    service.providers.list_active = AsyncMock(return_value=[active_provider])
    service.checks.find_reusable_for_file = AsyncMock(return_value=None)
    service.checks.create = AsyncMock(return_value=pending)

    with patch("app.modules.receipt_checks.service.FinanceService") as FS, \
         patch("app.modules.receipt_checks.service.build_client_for_provider") as build_client:
        FS.return_value = MagicMock()
        client = MagicMock()
        client.check_file = AsyncMock(
            return_value=ProviderCheckResult(
                refundable=True,
                error_code="upstream_error",
                error_message="HTTP 502",
            )
        )
        build_client.return_value = client

        service._charge_trader = AsyncMock()
        service._refund_trader = AsyncMock()

        result = await service.run_check_for_order(
            order=order, trader_user=trader_user, trigger=ReceiptCheckTrigger.MANUAL
        )

    service._charge_trader.assert_awaited_once()
    service._refund_trader.assert_awaited_once()
    assert result.status == ReceiptCheckStatus.FAILED
    assert result.error_code == "upstream_error"
    assert result.charged is True
    assert result.refunded is True


# ── Non-refundable error: charge stays, NO refund ───────────────────────


@pytest.mark.asyncio
async def test_run_check_non_refundable_error_keeps_charge(
    service, order, trader_user, active_provider
):
    pending = _make_check_row(status=ReceiptCheckStatus.PENDING)
    service.providers.list_active = AsyncMock(return_value=[active_provider])
    service.checks.find_reusable_for_file = AsyncMock(return_value=None)
    service.checks.create = AsyncMock(return_value=pending)

    with patch("app.modules.receipt_checks.service.FinanceService") as FS, \
         patch("app.modules.receipt_checks.service.build_client_for_provider") as build_client:
        FS.return_value = MagicMock()
        client = MagicMock()
        # Pretend the provider billed us despite returning a logical error.
        # In practice this shouldn't happen but the contract is: refundable=False
        # means we honor the charge.
        client.check_file = AsyncMock(
            return_value=ProviderCheckResult(
                refundable=False,
                error_code="strange_billed_error",
                error_message="we got billed somehow",
            )
        )
        build_client.return_value = client

        service._charge_trader = AsyncMock()
        service._refund_trader = AsyncMock()

        result = await service.run_check_for_order(
            order=order, trader_user=trader_user, trigger=ReceiptCheckTrigger.MANUAL
        )

    service._charge_trader.assert_awaited_once()
    service._refund_trader.assert_not_called()
    assert result.status == ReceiptCheckStatus.FAILED
    assert result.refunded is False
    assert result.charged is True


# ── Adapter exception is swallowed and treated as refundable ────────────


@pytest.mark.asyncio
async def test_run_check_adapter_exception_is_refundable(
    service, order, trader_user, active_provider
):
    pending = _make_check_row(status=ReceiptCheckStatus.PENDING)
    service.providers.list_active = AsyncMock(return_value=[active_provider])
    service.checks.find_reusable_for_file = AsyncMock(return_value=None)
    service.checks.create = AsyncMock(return_value=pending)

    with patch("app.modules.receipt_checks.service.FinanceService") as FS, \
         patch("app.modules.receipt_checks.service.build_client_for_provider") as build_client:
        FS.return_value = MagicMock()
        client = MagicMock()
        client.check_file = AsyncMock(side_effect=RuntimeError("adapter exploded"))
        build_client.return_value = client

        service._charge_trader = AsyncMock()
        service._refund_trader = AsyncMock()

        result = await service.run_check_for_order(
            order=order, trader_user=trader_user, trigger=ReceiptCheckTrigger.MANUAL
        )

    assert result.status == ReceiptCheckStatus.FAILED
    assert result.error_code == "adapter_exception"
    service._refund_trader.assert_awaited_once()


# ── Helpers ─────────────────────────────────────────────────────────────


def test_mask_api_key_empty_returns_empty():
    assert _mask_api_key("") == ""


def test_mask_api_key_short_string_falls_back_to_stars():
    # Anything shorter than 9 chars hides the entire key behind asterisks
    # rather than leaking a half-key tail.
    assert _mask_api_key("short") == "sk_live_*******"
    assert _mask_api_key("12345678") == "sk_live_*******"


def test_mask_api_key_returns_last_4_for_long_keys():
    assert _mask_api_key("sk_live_long_key_abcd") == "sk_live_***abcd"


def test_mask_provider_renders_masked_key(active_provider):
    masked = ReceiptCheckService.mask_provider(active_provider)
    assert masked["api_key_masked"] == "sk_live_***1234"
    assert masked["is_active"] is True
    assert masked["price_usdt"] == Decimal("0.50")


def test_mask_provider_without_tail_returns_none():
    p = MagicMock(spec=ReceiptCheckProvider)
    p.id = 2
    p.code = "x"
    p.name = "y"
    p.adapter_type = "trexo"
    p.is_active = False
    p.base_url = "https://example"
    p.api_key_tail = None
    p.price_usdt = Decimal("0")
    p.request_timeout_ms = 1000
    p.settings = {}
    p.created_at = "2026-01-01T00:00:00"
    p.updated_at = "2026-01-01T00:00:00"
    masked = ReceiptCheckService.mask_provider(p)
    assert masked["api_key_masked"] is None


# ── Provider CRUD: multiple providers may be active at once ─────────────


@pytest.mark.asyncio
async def test_create_provider_active_does_not_deactivate_others(service):
    """Multi-active: activating a provider must NOT disable the others — each
    active provider is a selectable option for traders."""
    service.providers.get_by_code = AsyncMock(return_value=None)
    service.providers.deactivate_all = AsyncMock()
    service.providers.create = AsyncMock(
        return_value=_make_check_row(id=10, code="trexo", is_active=True)
    )

    payload = ProviderCreate(
        code="trexo",
        name="TREXO",
        adapter_type=ReceiptCheckProviderAdapter.TREXO,
        base_url="https://api.trexo.example",
        api_key="sk_live_xxxx_yyyy",
        price_usdt=Decimal("0.5"),
        is_active=True,
    )

    with patch("app.modules.receipt_checks.service.encrypt_api_secret", return_value="enc::"):
        await service.create_provider(payload, admin_user_id=1)

    service.providers.deactivate_all.assert_not_called()
    service.providers.create.assert_awaited_once()
    created_payload = service.providers.create.call_args[0][0]
    assert created_payload["is_active"] is True
    assert created_payload["api_key_encrypted"] == "enc::"
    assert created_payload["api_key_tail"] == "yyyy"


@pytest.mark.asyncio
async def test_create_provider_does_not_deactivate_when_inactive(service):
    service.providers.get_by_code = AsyncMock(return_value=None)
    service.providers.deactivate_all = AsyncMock()
    service.providers.create = AsyncMock(
        return_value=_make_check_row(id=11, code="trexo", is_active=False)
    )

    payload = ProviderCreate(
        code="trexo",
        name="TREXO",
        adapter_type=ReceiptCheckProviderAdapter.TREXO,
        base_url="https://api.trexo.example",
        api_key="sk_live_test_key_abcd",
        price_usdt=Decimal("0.5"),
        is_active=False,
    )

    with patch("app.modules.receipt_checks.service.encrypt_api_secret", return_value="enc::"):
        await service.create_provider(payload, admin_user_id=1)

    service.providers.deactivate_all.assert_not_called()


@pytest.mark.asyncio
async def test_create_provider_rejects_duplicate_code(service, active_provider):
    service.providers.get_by_code = AsyncMock(return_value=active_provider)

    payload = ProviderCreate(
        code="trexo",
        name="TREXO",
        adapter_type=ReceiptCheckProviderAdapter.TREXO,
        base_url="https://api.trexo.example",
        api_key="sk_live_xxxxxxxxxxxx",
        price_usdt=Decimal("0.5"),
    )

    with pytest.raises(ConflictException):
        await service.create_provider(payload, admin_user_id=1)


@pytest.mark.asyncio
async def test_update_provider_keeps_existing_api_key_when_omitted(service, active_provider):
    service.providers.get = AsyncMock(return_value=active_provider)
    service.providers.deactivate_all = AsyncMock()
    service.providers.update = AsyncMock(return_value=active_provider)

    payload = ProviderUpdate(name="TREXO renamed")
    with patch("app.modules.receipt_checks.service.encrypt_api_secret") as enc:
        await service.update_provider(1, payload, admin_user_id=1)
        enc.assert_not_called()

    service.providers.update.assert_awaited_once()
    update_payload = service.providers.update.call_args[0][1]
    assert "api_key_encrypted" not in update_payload
    assert update_payload["name"] == "TREXO renamed"


@pytest.mark.asyncio
async def test_update_provider_activating_does_not_deactivate_others(service, active_provider):
    active_provider.is_active = False
    service.providers.get = AsyncMock(return_value=active_provider)
    service.providers.deactivate_all = AsyncMock()
    service.providers.update = AsyncMock(return_value=active_provider)

    await service.update_provider(1, ProviderUpdate(is_active=True), admin_user_id=1)
    service.providers.deactivate_all.assert_not_called()
    update_payload = service.providers.update.call_args[0][1]
    assert update_payload["is_active"] is True


@pytest.mark.asyncio
async def test_delete_provider_not_found(service):
    service.providers.get = AsyncMock(return_value=None)
    with pytest.raises(NotFoundException):
        await service.delete_provider(999, admin_user_id=1)


# ── _charge_trader: rejects non-trader role ─────────────────────────────


@pytest.mark.asyncio
async def test_charge_receipt_check_fee_rejects_non_trader_role():
    """The fee money move (incl. the trader-role guard) lives in FinanceService;
    the role check fires before any balance/session access."""
    from app.modules.finance.service import FinanceService

    admin = MagicMock(spec=User)
    admin.role = UserRole.ADMIN
    finance = FinanceService(MagicMock())

    with pytest.raises(ValidationException):
        await finance.charge_receipt_check_fee(admin, Decimal("1"), check_id=1)


@pytest.mark.asyncio
async def test_run_check_replays_on_concurrent_insert_conflict(service, order, trader_user, active_provider):
    """A concurrent second run (manual + auto trigger / double-click) conflicts
    on the live-check unique index → the service replays the winner instead of
    charging the trader's WORK a second time (#18)."""
    from sqlalchemy.exc import IntegrityError

    service.providers.list_active = AsyncMock(return_value=[active_provider])
    service.checks.find_reusable_for_file = AsyncMock(return_value=None)  # no FINISHED dedup hit
    service.checks.create = AsyncMock(side_effect=IntegrityError("INSERT", {}, Exception("dup")))
    winner = _make_check_row(id=555, status=ReceiptCheckStatus.PENDING)
    service.checks.find_active_for_file = AsyncMock(return_value=winner)
    service._charge_trader = AsyncMock()

    result = await service.run_check_for_order(order, trader_user, ReceiptCheckTrigger.MANUAL)

    assert result is winner
    service._charge_trader.assert_not_called()           # no double charge
    service.checks.find_active_for_file.assert_awaited_once()


# ── resolve_provider_for_trader: which provider a check runs with ───────


def _provider(pid: int, active: bool = True, price: str = "0.50") -> MagicMock:
    p = MagicMock(spec=ReceiptCheckProvider)
    p.id = pid
    p.is_active = active
    p.price_usdt = Decimal(price)
    p.name = f"P{pid}"
    return p


def _trader(default_id=None) -> MagicMock:
    t = MagicMock()
    t.default_receipt_check_provider_id = default_id
    return t


@pytest.mark.asyncio
async def test_resolve_explicit_active_provider_returns_it(service):
    p = _provider(3, active=True)
    service.providers.get = AsyncMock(return_value=p)
    result = await service.resolve_provider_for_trader(_trader(), requested_id=3)
    assert result is p
    service.providers.get.assert_awaited_once_with(3)


@pytest.mark.asyncio
async def test_resolve_explicit_inactive_provider_raises(service):
    service.providers.get = AsyncMock(return_value=_provider(3, active=False))
    with pytest.raises(ValidationException):
        await service.resolve_provider_for_trader(_trader(), requested_id=3)


@pytest.mark.asyncio
async def test_resolve_explicit_missing_provider_raises(service):
    service.providers.get = AsyncMock(return_value=None)
    with pytest.raises(ValidationException):
        await service.resolve_provider_for_trader(_trader(), requested_id=999)


@pytest.mark.asyncio
async def test_resolve_default_active_provider_returns_default(service):
    default = _provider(5, active=True)
    service.providers.get = AsyncMock(return_value=default)
    service.providers.list_active = AsyncMock(return_value=[_provider(9)])
    result = await service.resolve_provider_for_trader(_trader(default_id=5), requested_id=None)
    assert result is default
    service.providers.get.assert_awaited_once_with(5)


@pytest.mark.asyncio
async def test_resolve_inactive_default_falls_back_to_first_active(service):
    inactive_default = _provider(5, active=False)
    first_active = _provider(2, active=True)
    service.providers.get = AsyncMock(return_value=inactive_default)
    service.providers.list_active = AsyncMock(return_value=[first_active, _provider(7)])
    result = await service.resolve_provider_for_trader(_trader(default_id=5), requested_id=None)
    assert result is first_active


@pytest.mark.asyncio
async def test_resolve_no_default_returns_first_active(service):
    first_active = _provider(2, active=True)
    service.providers.list_active = AsyncMock(return_value=[first_active])
    result = await service.resolve_provider_for_trader(_trader(default_id=None), requested_id=None)
    assert result is first_active


@pytest.mark.asyncio
async def test_resolve_no_active_providers_returns_none(service):
    service.providers.list_active = AsyncMock(return_value=[])
    result = await service.resolve_provider_for_trader(_trader(default_id=None), requested_id=None)
    assert result is None


@pytest.mark.asyncio
async def test_run_check_uses_explicit_provider_over_get_active(service, order, trader_user):
    """When the caller passes a provider, that provider (its id + price) is used
    and the global get_active() is never consulted."""
    explicit = _provider(77, active=True, price="1.25")
    # get_active would return a DIFFERENT provider — must not be used.
    service.providers.get_active = AsyncMock(return_value=_provider(1, price="0.50"))
    service.checks.find_reusable_for_file = AsyncMock(return_value=None)
    service.checks.create = AsyncMock(
        return_value=_make_check_row(status=ReceiptCheckStatus.PENDING, provider_id=77)
    )

    with patch("app.modules.receipt_checks.service.FinanceService"), \
         patch("app.modules.receipt_checks.service.build_client_for_provider") as build_client:
        client = MagicMock()
        client.check_file = AsyncMock(
            return_value=ProviderCheckResult(
                is_clean=True, verdict=[], parsed_data={}, refundable=False
            )
        )
        build_client.return_value = client
        service._charge_trader = AsyncMock()
        service._refund_trader = AsyncMock()

        await service.run_check_for_order(
            order, trader_user, ReceiptCheckTrigger.MANUAL, provider=explicit
        )

    service.providers.get_active.assert_not_called()
    payload = service.checks.create.call_args[0][0]
    assert payload["provider_id"] == 77
    assert payload["price_usdt"] == Decimal("1.25")
