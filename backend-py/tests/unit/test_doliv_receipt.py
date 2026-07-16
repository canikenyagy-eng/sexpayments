"""Долив RECEIPT feature — the доливщик attaches a check on execute; it's stored,
served to the requester, and pushed to the requester's telegram bot.

Covers the layers the service-level test_doliv_service tests don't reach:
  * the ``POST /doliv/{uuid}/execute`` endpoint — multipart read, format/size
    validation (reject non photo/PDF), ReceiptStorage.save, settle;
  * the ``GET /doliv/{uuid}/receipt`` endpoint — FileResponse + access guard;
  * the ``notify_requester_doliv_receipt`` celery task body — resolves the
    REQUESTER's group, POSTs the file to /doliv_check, and the no-group branch;
  * the ``_build_doliv_requisite_payload`` helper.

Endpoint functions are called directly (the project has no HTTP test harness),
which still exercises all the multipart/validation/save logic.
"""
from __future__ import annotations

import io
import os
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import UploadFile
from fastapi.responses import FileResponse

from app.api.v1.endpoints.doliv import execute_doliv, get_doliv_receipt
from app.common.enums.balances import BalanceType
from app.common.enums.finances import Currency
from app.common.enums.payments import PaymentMethod
from app.common.enums.payouts import PayoutStatus
from app.common.enums.cascading import RequisiteSource
from app.common.enums.rates import OrderBookSide
from app.common.enums.requisites import RequisiteStatus
from app.common.enums.users import UserRole
from app.core.exceptions import ValidationException
from app.modules.doliv.exceptions import DolivForbidden
from app.modules.doliv.service import DolivService
from app.modules.finance.models import Balance
from app.modules.rates.models import RateConfig
from app.modules.requisites.models import Requisite, RequisiteLimit
from app.modules.settings.service import SettingsService
from app.modules.users.models import User
from app.workers.tasks.trader_bot import (
    _build_doliv_requisite_payload,
    notify_requester_doliv_receipt,
)

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


# ── builders ───────────────────────────────────────────────────────────


async def _mk_user(session, username, role=UserRole.TRADER) -> User:
    u = User(username=username, password="x", role=role, is_system=False, is_blocked=False)
    session.add(u)
    await session.flush()
    return u


async def _set_work(session, user_id, amount) -> None:
    session.add(Balance(user_id=user_id, type=BalanceType.WORK, currency=Currency.USDT, amount=Decimal(amount)))
    await session.flush()


async def _mk_requisite(session, trader_id) -> Requisite:
    req = Requisite(
        trader_id=trader_id, nickname="r", bank_name="Sber", account_number="40817000",
        account_holder="Ivan", payment_method=PaymentMethod.SBP,
        status=RequisiteStatus.ENABLED, currency=Currency.RUB, is_active=True,
        is_archived=False, source=RequisiteSource.LOCAL,
    )
    session.add(req)
    await session.flush()
    session.add(RequisiteLimit(
        requisite_id=req.id, limit_daily=Decimal("100000"), limit_monthly=Decimal("100000000"),
        current_daily_turnover=Decimal("0"), current_monthly_turnover=Decimal("0"),
    ))
    await session.flush()
    return req


async def _configure(session, executor_id) -> None:
    s = SettingsService(session)
    await s.set("doliv_executor_user_ids", str(executor_id))
    await s.set("doliv_price_percent", "10")
    await s.set("doliv_executor_reward_percent", "5")
    await s.set("doliv_min_amount", "0")
    await s.set("doliv_max_amount", "0")
    session.add(RateConfig(name="r", side=OrderBookSide.SELL, fiat_currency=Currency.RUB,
                           is_active=True, current_rate=100.0))
    await session.flush()


def _upload(content: bytes, filename: str) -> UploadFile:
    return UploadFile(file=io.BytesIO(content), filename=filename)


async def _claimed_doliv(session):
    """requester + executor + a CLAIMED долив ready to execute."""
    requester = await _mk_user(session, "req")
    executor = await _mk_user(session, "exe")
    await _set_work(session, requester.id, "1000")
    req = await _mk_requisite(session, requester.id)
    await _configure(session, executor.id)
    svc = DolivService(session)
    doliv = await svc.create(requester, req.id, Decimal("100"))
    await svc.claim(executor, str(doliv.uuid))
    return requester, executor, doliv


async def _status(session, doliv_id):
    from app.modules.payouts.models import Payout
    return (await session.get(Payout, doliv_id)).status


# ── POST /execute — multipart upload, validation, save ───────────────────


@pytest.mark.asyncio
async def test_execute_endpoint_saves_valid_pdf_and_completes(session):
    requester, executor, doliv = await _claimed_doliv(session)
    with patch("app.modules.doliv.service.celery_app"):
        resp = await execute_doliv(
            str(doliv.uuid), attachment=_upload(PDF, "check.pdf"),
            current_user=executor, session=session,
        )
    assert resp.status == PayoutStatus.COMPLETED
    assert resp.has_receipt is True
    # File actually persisted under UPLOAD_DIR.
    from app.modules.payouts.models import Payout
    stored = (await session.get(Payout, doliv.id)).receipt_file
    assert stored and os.path.isfile(stored)
    assert open(stored, "rb").read() == PDF


@pytest.mark.asyncio
async def test_execute_endpoint_accepts_png(session):
    requester, executor, doliv = await _claimed_doliv(session)
    with patch("app.modules.doliv.service.celery_app"):
        resp = await execute_doliv(
            str(doliv.uuid), attachment=_upload(PNG, "check.png"),
            current_user=executor, session=session,
        )
    assert resp.status == PayoutStatus.COMPLETED and resp.has_receipt is True


@pytest.mark.asyncio
async def test_execute_endpoint_rejects_unsupported_format_no_settle(session):
    """A .txt (or any non photo/PDF) is rejected BEFORE settle — долив stays
    CLAIMED, no receipt, no money moved."""
    requester, executor, doliv = await _claimed_doliv(session)
    with patch("app.modules.doliv.service.celery_app"):
        with pytest.raises(ValidationException):
            await execute_doliv(
                str(doliv.uuid), attachment=_upload(b"hello", "check.txt"),
                current_user=executor, session=session,
            )
    assert await _status(session, doliv.id) == PayoutStatus.CLAIMED
    # requester still frozen (no settle), executor not credited.
    from app.modules.payouts.models import Payout
    assert (await session.get(Payout, doliv.id)).receipt_file is None


@pytest.mark.asyncio
async def test_execute_endpoint_rejects_content_extension_mismatch(session):
    """A PDF renamed .png (content ≠ extension) is rejected."""
    requester, executor, doliv = await _claimed_doliv(session)
    with patch("app.modules.doliv.service.celery_app"):
        with pytest.raises(ValidationException):
            await execute_doliv(
                str(doliv.uuid), attachment=_upload(PDF, "check.png"),
                current_user=executor, session=session,
            )
    assert await _status(session, doliv.id) == PayoutStatus.CLAIMED


# ── GET /receipt — serve + access guard ──────────────────────────────────


@pytest.mark.asyncio
async def test_receipt_endpoint_serves_file_to_requester_and_executor(session):
    requester, executor, doliv = await _claimed_doliv(session)
    with patch("app.modules.doliv.service.celery_app"):
        await execute_doliv(str(doliv.uuid), attachment=_upload(PDF, "c.pdf"),
                            current_user=executor, session=session)
    for who in (requester, executor):
        resp = await get_doliv_receipt(str(doliv.uuid), current_user=who, session=session)
        assert isinstance(resp, FileResponse)
        assert os.path.isfile(resp.path)


@pytest.mark.asyncio
async def test_receipt_endpoint_forbidden_for_outsider(session):
    requester, executor, doliv = await _claimed_doliv(session)
    intruder = await _mk_user(session, "intruder")
    with patch("app.modules.doliv.service.celery_app"):
        await execute_doliv(str(doliv.uuid), attachment=_upload(PDF, "c.pdf"),
                            current_user=executor, session=session)
    with pytest.raises(DolivForbidden):
        await get_doliv_receipt(str(doliv.uuid), current_user=intruder, session=session)


def test_doliv_resp_hides_executor_reward_from_requester():
    """The доливщик's reward (executor_reward_usdt) is exposed to the executor /
    pool views but HIDDEN from the requester — a regular trader (create / mine /
    cancel) sees only what THEY pay (the price), never the доливщик's cut."""
    from datetime import datetime

    from app.api.v1.endpoints.doliv import _doliv_resp
    from app.modules.payouts.models import Payout

    p = Payout(
        uuid=uuid4(), status=PayoutStatus.COMPLETED, amount=Decimal("1000"),
        currency=Currency.RUB, payment_method=PaymentMethod.SBP,
        trader_fee_usdt=Decimal("0.06"), created_at=datetime(2026, 6, 1),
    )
    # Executor / pool view keeps the reward.
    assert _doliv_resp(p, {}).executor_reward_usdt == 0.06
    # Requester view hides it.
    assert _doliv_resp(p, {}, hide_reward=True).executor_reward_usdt is None


# ── notify_requester_doliv_receipt task body ─────────────────────────────


def _fake_doliv(**over):
    base = dict(
        is_doliv=True, requester_trader_id=7, receipt_file="/up/r.pdf",
        uuid=uuid4(), amount=Decimal("100"), currency=Currency.RUB,
        payment_method=PaymentMethod.SBP, req_number="40817810099910000001",
        req_holder="Ivan", req_extra="Sber",
    )
    base.update(over)
    return SimpleNamespace(**base)


def _patch_task(monkeypatch, *, doliv, group_id):
    """Wire the task's SessionLocal / settings to fakes. Returns the captured POST."""
    captured: dict = {}

    class _SessionCtx:
        async def __aenter__(self_):
            s = MagicMock()
            s.get = AsyncMock(return_value=doliv)
            res = MagicMock()
            res.scalar_one_or_none = MagicMock(return_value=group_id)
            s.execute = AsyncMock(return_value=res)
            return s

        async def __aexit__(self_, *a):
            return False

    class _Client:
        def __init__(self_, *a, **k): pass
        async def __aenter__(self_): return self_
        async def __aexit__(self_, *a): return False
        async def post(self_, url, json, headers):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return MagicMock(raise_for_status=MagicMock())

    monkeypatch.setattr("app.workers.tasks.trader_bot.SessionLocal", lambda: _SessionCtx())
    monkeypatch.setattr("app.workers.tasks.trader_bot.httpx.AsyncClient", _Client)
    fake_settings = SimpleNamespace(TRADER_BOT_URL="http://bot", TRADER_BOT_SECRET="s3cr3t")
    monkeypatch.setattr("app.workers.tasks.trader_bot.get_settings", lambda: fake_settings)
    return captured


def test_notify_task_posts_receipt_to_requester_bot(monkeypatch):
    doliv = _fake_doliv()
    captured = _patch_task(monkeypatch, doliv=doliv, group_id=-100500)

    notify_requester_doliv_receipt(1)

    assert captured["url"] == "http://bot/doliv_check"
    assert captured["headers"]["X-Bot-Secret"] == "s3cr3t"
    body = captured["json"]
    assert body["chat_id"] == -100500                    # the REQUESTER's group
    assert body["file_paths"] == ["/up/r.pdf"]
    assert body["doliv_uuid"] == str(doliv.uuid)
    assert body["requisite"]["account_number"] == "40817810099910000001"


def test_notify_task_skips_when_no_telegram_group(monkeypatch):
    doliv = _fake_doliv()
    captured = _patch_task(monkeypatch, doliv=doliv, group_id=None)  # requester has no group
    notify_requester_doliv_receipt(1)
    assert captured == {}  # no POST attempted


def test_notify_task_skips_when_no_bot_url(monkeypatch):
    monkeypatch.setattr(
        "app.workers.tasks.trader_bot.get_settings",
        lambda: SimpleNamespace(TRADER_BOT_URL=None, TRADER_BOT_SECRET=None),
    )
    # Must not raise / must not touch the DB.
    notify_requester_doliv_receipt(1)


def test_build_doliv_requisite_payload():
    assert _build_doliv_requisite_payload(_fake_doliv())["bank_name"] == "Sber"
    # Nothing stored → None (the bot renders without a requisite block).
    assert _build_doliv_requisite_payload(
        _fake_doliv(req_number=None, req_holder=None, req_extra=None)
    ) is None
