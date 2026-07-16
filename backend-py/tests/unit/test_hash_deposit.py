"""Unit tests for the admin "top-up by TRC20 hash" flow.

Covers the two risky, pure-ish parts:
  * base58 TRON address encoding (hex → T-address),
  * ``FinanceService.verify_hash_deposit`` validation branches (with the TronGrid
    client + the "hash already used" check mocked out).

The credit itself (``confirm_hash_deposit`` → ledger) is a thin wrapper over the
already-tested ``transfer``; its guard (reference_id = hash) is exercised here via
``_hash_deposit_exists``.
"""
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import ValidationException
from app.infrastructure.tron.client import (
    Trc20Transfer,
    TronGridError,
    TxInfo,
    hex_to_tron_address,
)
from app.modules.finance.service import FinanceService

USDT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
HASH = "a" * 64
_INFO_PATCH = "app.infrastructure.tron.client.TronGridClient.get_transaction_info"


# ── base58 address encoding ────────────────────────────────────────────

def test_hex_to_tron_address_bare_contract():
    assert hex_to_tron_address("a614f803b6fd780986a42c78ec9c7f77e6ded13c") == USDT


def test_hex_to_tron_address_topic_word():
    # 32-byte left-padded event topic → same T-address.
    topic = "000000000000000000000000a614f803b6fd780986a42c78ec9c7f77e6ded13c"
    assert hex_to_tron_address(topic) == USDT


def test_hex_to_tron_address_with_41_prefix():
    assert hex_to_tron_address("41a614f803b6fd780986a42c78ec9c7f77e6ded13c") == USDT


# ── tx-hash normalization ──────────────────────────────────────────────

@pytest.mark.parametrize("bad", ["", "0x123", "z" * 64, "a" * 63, "a" * 65, None])
def test_normalize_tx_hash_rejects_bad(bad):
    with pytest.raises(ValidationException):
        FinanceService._normalize_tx_hash(bad)


def test_normalize_tx_hash_strips_0x_and_lowercases():
    assert FinanceService._normalize_tx_hash("0x" + "A" * 64) == "a" * 64


# ── verify_hash_deposit branches ───────────────────────────────────────

def _svc(hash_used: bool = False) -> FinanceService:
    svc = FinanceService(session=MagicMock())
    svc._hash_deposit_exists = AsyncMock(return_value=hash_used)
    return svc


def _info(**kw) -> TxInfo:
    defaults = dict(
        found=True,
        confirmed=True,
        success=True,
        transfers=[
            Trc20Transfer(
                contract_address=USDT,
                from_address="Tfrom",
                to_address="Tto",
                amount=Decimal("12.5"),
            )
        ],
    )
    defaults.update(kw)
    return TxInfo(**defaults)


@pytest.mark.asyncio
async def test_verify_happy_path():
    svc = _svc()
    with patch(_INFO_PATCH, AsyncMock(return_value=_info())):
        out = await svc.verify_hash_deposit("0x" + "A" * 64)
    assert out["amount"] == Decimal("12.5")
    assert out["to_address"] == "Tto"
    assert out["from_address"] == "Tfrom"
    assert out["tx_hash"] == "a" * 64  # normalized


@pytest.mark.asyncio
async def test_verify_rejects_already_used_hash():
    svc = _svc(hash_used=True)
    # TronGrid must not even be consulted once the hash is known-used.
    info = AsyncMock(return_value=_info())
    with patch(_INFO_PATCH, info):
        with pytest.raises(ValidationException):
            await svc.verify_hash_deposit(HASH)
    info.assert_not_called()


@pytest.mark.asyncio
async def test_verify_rejects_not_found():
    svc = _svc()
    with patch(_INFO_PATCH, AsyncMock(return_value=_info(found=False, transfers=[]))):
        with pytest.raises(ValidationException):
            await svc.verify_hash_deposit(HASH)


@pytest.mark.asyncio
async def test_verify_rejects_unconfirmed():
    svc = _svc()
    with patch(_INFO_PATCH, AsyncMock(return_value=_info(confirmed=False))):
        with pytest.raises(ValidationException):
            await svc.verify_hash_deposit(HASH)


@pytest.mark.asyncio
async def test_verify_rejects_failed_tx():
    svc = _svc()
    with patch(_INFO_PATCH, AsyncMock(return_value=_info(success=False))):
        with pytest.raises(ValidationException):
            await svc.verify_hash_deposit(HASH)


@pytest.mark.asyncio
async def test_verify_rejects_non_usdt_transfer():
    svc = _svc()
    other = _info(transfers=[
        Trc20Transfer(contract_address="TSomeOtherToken1111111111111111111",
                      from_address="Tf", to_address="Tt", amount=Decimal("5"))
    ])
    with patch(_INFO_PATCH, AsyncMock(return_value=other)):
        with pytest.raises(ValidationException):
            await svc.verify_hash_deposit(HASH)


@pytest.mark.asyncio
async def test_verify_rejects_zero_amount():
    svc = _svc()
    zero = _info(transfers=[
        Trc20Transfer(contract_address=USDT, from_address="Tf", to_address="Tt", amount=Decimal("0"))
    ])
    with patch(_INFO_PATCH, AsyncMock(return_value=zero)):
        with pytest.raises(ValidationException):
            await svc.verify_hash_deposit(HASH)


@pytest.mark.asyncio
async def test_verify_rejects_tron_unreachable():
    svc = _svc()
    with patch(_INFO_PATCH, AsyncMock(side_effect=TronGridError("boom"))):
        with pytest.raises(ValidationException):
            await svc.verify_hash_deposit(HASH)
