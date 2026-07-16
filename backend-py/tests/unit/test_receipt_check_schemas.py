"""Schema-level tests for the receipt-check module.

These pin the contract between the ORM rows and the API payload: the
serializer in `ReceiptCheckResponse.from_orm_check` and the URL validator in
`ProviderBase`.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from app.common.enums.receipt_checks import (
    ReceiptCheckProviderAdapter,
    ReceiptCheckStatus,
    ReceiptCheckTrigger,
)
from app.modules.receipt_checks.models import ReceiptCheck
from app.modules.receipt_checks.schemas import (
    ProviderCreate,
    ProviderUpdate,
    ReceiptCheckResponse,
)


def _make_orm_row(**overrides) -> MagicMock:
    base = dict(
        id=1,
        order_id=42,
        provider_id=7,
        trader_user_id=99,
        trigger=ReceiptCheckTrigger.MANUAL.value,
        status=ReceiptCheckStatus.SUCCESS,
        is_clean=False,
        verdict=[{"type": "FAKE_PROOF"}],
        parsed_data={"sum": "1000"},
        provider_check_id="ck-1",
        error_code=None,
        error_message=None,
        price_usdt=Decimal("0.5000"),
        charged=True,
        refunded=False,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        finished_at=datetime(2026, 1, 1, 0, 0, 5, tzinfo=timezone.utc),
    )
    base.update(overrides)
    row = MagicMock(spec=ReceiptCheck)
    for k, v in base.items():
        setattr(row, k, v)
    return row


def test_from_orm_check_maps_all_fields_into_pydantic():
    orm = _make_orm_row()
    resp = ReceiptCheckResponse.from_orm_check(orm)

    assert resp.id == 1
    assert resp.order_id == 42
    assert resp.provider_id == 7
    assert resp.status == ReceiptCheckStatus.SUCCESS
    assert resp.trigger == ReceiptCheckTrigger.MANUAL
    assert resp.is_clean is False
    assert resp.charged is True
    assert resp.refunded is False
    assert resp.price_usdt == Decimal("0.5000")
    assert resp.verdict[0].type == "FAKE_PROOF"


def test_from_orm_check_handles_pending_state():
    orm = _make_orm_row(
        status=ReceiptCheckStatus.PENDING,
        is_clean=None,
        verdict=None,
        parsed_data=None,
        provider_check_id=None,
        finished_at=None,
    )
    resp = ReceiptCheckResponse.from_orm_check(orm)
    assert resp.status == ReceiptCheckStatus.PENDING
    assert resp.is_clean is None
    assert resp.verdict is None
    assert resp.finished_at is None


def test_from_orm_check_handles_failed_state_with_error():
    orm = _make_orm_row(
        status=ReceiptCheckStatus.FAILED,
        is_clean=None,
        verdict=None,
        error_code="upstream_error",
        error_message="HTTP 502",
        charged=True,
        refunded=True,
    )
    resp = ReceiptCheckResponse.from_orm_check(orm)
    assert resp.status == ReceiptCheckStatus.FAILED
    assert resp.error_code == "upstream_error"
    assert resp.refunded is True


# ── ProviderCreate validation ───────────────────────────────────────────


def test_provider_create_rejects_non_http_base_url():
    with pytest.raises(ValidationError):
        ProviderCreate(
            code="trexo",
            name="TREXO",
            adapter_type=ReceiptCheckProviderAdapter.TREXO,
            base_url="ftp://api.trexo.example",
            api_key="sk_live_x",
            price_usdt=Decimal("0.5"),
        )


def test_provider_create_strips_trailing_slash():
    p = ProviderCreate(
        code="trexo",
        name="TREXO",
        adapter_type=ReceiptCheckProviderAdapter.TREXO,
        base_url="https://api.trexo.example/",
        api_key="sk_live_x",
        price_usdt=Decimal("0.5"),
    )
    assert p.base_url == "https://api.trexo.example"


def test_provider_create_rejects_negative_price():
    with pytest.raises(ValidationError):
        ProviderCreate(
            code="trexo",
            name="TREXO",
            adapter_type=ReceiptCheckProviderAdapter.TREXO,
            base_url="https://api.trexo.example",
            api_key="sk_live_x",
            price_usdt=Decimal("-1"),
        )


def test_provider_create_rejects_unreasonable_price():
    with pytest.raises(ValidationError):
        ProviderCreate(
            code="trexo",
            name="TREXO",
            adapter_type=ReceiptCheckProviderAdapter.TREXO,
            base_url="https://api.trexo.example",
            api_key="sk_live_x",
            price_usdt=Decimal("999999"),
        )


def test_provider_create_rejects_invalid_code():
    """Codes are constrained to lowercase letters/digits/underscores/dashes and
    must start with a letter (used as a path-friendly identifier in logs)."""
    with pytest.raises(ValidationError):
        ProviderCreate(
            code="9bad",
            name="x",
            adapter_type=ReceiptCheckProviderAdapter.TREXO,
            base_url="https://api.trexo.example",
            api_key="sk_live_x",
            price_usdt=Decimal("0.5"),
        )


def test_provider_update_optional_url_still_validated_when_provided():
    with pytest.raises(ValidationError):
        ProviderUpdate(base_url="javascript:alert(1)")


def test_provider_update_allows_omitting_everything():
    # All fields are optional in update; empty payload validates fine.
    upd = ProviderUpdate()
    assert upd.model_dump(exclude_unset=True) == {}
