import pytest
from unittest.mock import AsyncMock, MagicMock

from app.common.enums.disputes import DisputeReason, DisputeStatus
from app.common.enums.orders import OrderStatus
from app.common.enums.receipts import ReceiptUploader
from app.common.enums.users import UserRole
from app.core.exceptions import NotFoundException, ValidationException
from app.modules.disputes.models import Dispute
from app.modules.disputes.schemas import DisputeCreate
from app.modules.disputes.service import DisputeService
from app.modules.merchants.models import Merchant
from app.modules.orders.models import Order
# Importing these makes ``DisputeResponse.model_validate`` work in
# isolation: validate triggers Order's mapper init, which resolves the
# string-based relationships to PaymentOption / Requisite / User.
from app.modules.payments.models import PaymentOption  # noqa: F401
from app.modules.requisites.models import Requisite  # noqa: F401
from app.modules.users.models import User  # noqa: F401


@pytest.fixture
def mock_session():
    session = MagicMock()

    class AsyncContextManagerMock:
        async def __aenter__(self):
            return session
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    session.begin.return_value = AsyncContextManagerMock()
    session.begin_nested.return_value = AsyncContextManagerMock()
    session.get = AsyncMock(return_value=None)
    session.execute = AsyncMock()
    session.flush = AsyncMock()
    return session


@pytest.fixture
def service(mock_session):
    svc = DisputeService(mock_session)
    svc.repository = AsyncMock()
    svc.order_repository = AsyncMock()
    svc.audit_log = AsyncMock()
    return svc


@pytest.fixture
def stub_celery(mocker):
    """Stub the Celery broker so unit tests don't try to serialise mocks.

    The disputes service enqueues, depending on the path:
      * ``notify_trader_new_dispute`` (open),
      * ``cascade.forward_dispute_to_provider`` (open),
      * ``callbacks.send_order_callback`` (open / resolve / reject — the
        merchant order webhook).

    All of them resolve through ``app.workers.celery_app.celery_app`` (or the
    trader_bot task factory). We replace both with no-op mocks and return the
    celery stub so individual tests can assert what was enqueued.
    """
    trader_bot_stub = MagicMock()
    trader_bot_stub.apply_async = MagicMock(return_value=None)
    trader_bot_stub.delay = MagicMock(return_value=None)
    mocker.patch(
        "app.workers.tasks.trader_bot.notify_trader_new_dispute",
        trader_bot_stub,
        create=True,
    )
    celery_stub = MagicMock()
    celery_stub.send_task = MagicMock(return_value=None)
    mocker.patch("app.workers.celery_app.celery_app", celery_stub, create=True)
    return celery_stub


@pytest.fixture(autouse=True)
def _auto_stub_celery(stub_celery):
    """Apply the celery stub to every test in this module."""
    return stub_celery


def _callback_order_ids(celery_stub) -> list[int]:
    """Order ids passed to send_order_callback across all send_task calls."""
    out = []
    for call in celery_stub.send_task.call_args_list:
        if call.args and call.args[0].endswith("callbacks.send_order_callback"):
            out.append(call.kwargs.get("args", call.args[1:])[0])
    return out


@pytest.fixture
def fake_finance(mocker):
    """Patch FinanceService so dispute money flows are observable no-ops."""
    fin = MagicMock()
    fin.reconcile_for_dispute = AsyncMock()
    fin.complete_order = AsyncMock()
    fin.cancel_order = AsyncMock()
    mocker.patch("app.modules.finance.service.FinanceService", return_value=fin)
    return fin


@pytest.fixture
def stub_change_status(mocker):
    """Mock OrderService.change_status — the single order→status funnel that the
    dispute-open paths delegate the DISPUTED transition (+ escrow reconcile) to.
    Unit tests assert *delegation*; the per-pre-status escrow math is covered by
    the dispute money-flow integration tests."""
    cs = AsyncMock(side_effect=lambda order, new_status, **kwargs: order)
    mocker.patch("app.modules.orders.service.OrderService.change_status", cs)
    return cs


@pytest.fixture
def fake_teamlead(mocker):
    tl = MagicMock()
    tl.calculate_and_pay_rewards = AsyncMock()
    tl.reverse_rewards = AsyncMock()
    mocker.patch("app.modules.teamleaders.service.TeamleaderService", return_value=tl)
    return tl


@pytest.fixture
def fake_requisite_limits(mocker):
    repo = MagicMock()
    repo.increment_turnover_by_order = AsyncMock()
    repo.decrement_turnover_by_order = AsyncMock()
    mocker.patch(
        "app.modules.requisites.repository.RequisiteLimitRepository",
        return_value=repo,
    )
    return repo


@pytest.fixture
def mock_merchant():
    merchant = MagicMock(spec=Merchant)
    merchant.id = 1
    merchant.user_id = 99
    return merchant


@pytest.fixture
def mock_order():
    order = MagicMock(spec=Order)
    order.id = 10
    # FAILED is a final status → disputable. Its reconcile path is the simple
    # "re-freeze trader collateral" branch (no reward/turnover reversal), which
    # keeps the open-path tests focused on orchestration.
    order.status = OrderStatus.FAILED
    order.trader_id = 20
    return order


# ────────────────────────────────────────────────────────────────
# open_dispute_by_merchant
# ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_open_dispute_by_merchant_success_order_id(
    service, mock_merchant, mock_order, fake_finance, stub_celery, stub_change_status
):
    service.order_repository.get_by_uuid_and_merchant.return_value = mock_order
    service.repository.get_by_order_id.return_value = None

    mock_dispute = MagicMock(spec=Dispute)
    mock_dispute.id = 100
    service.repository.create.return_value = mock_dispute

    data = DisputeCreate(
        reason=DisputeReason.UNKNOWN,
        evidence_files=["file1.png"],
    )

    result = await service.open_dispute_by_merchant(mock_merchant, data, order_id="some-uuid")

    assert result == mock_dispute
    service.order_repository.get_by_uuid_and_merchant.assert_called_once_with("some-uuid", mock_merchant.id)
    service.repository.create.assert_called_once()
    # Order → DISPUTED (+ escrow reconcile) delegated to the status funnel.
    stub_change_status.assert_awaited_once()
    assert stub_change_status.await_args.args[1] == OrderStatus.DISPUTED
    assert stub_change_status.await_args.kwargs["audit_action"] is None
    assert stub_change_status.await_args.kwargs["fire_callback"] is False
    # Merchant webhook fired for the DISPUTED transition.
    assert mock_order.id in _callback_order_ids(stub_celery)
    service.audit_log.assert_called_once()
    audit_kwargs = service.audit_log.call_args.kwargs
    assert audit_kwargs["action"] == "open_dispute"
    assert audit_kwargs["new_values"]["reason"] == DisputeReason.UNKNOWN


@pytest.mark.asyncio
async def test_open_dispute_persists_multiple_evidence_links(
    service, mock_merchant, mock_order, fake_finance, stub_celery, stub_change_status
):
    """A merchant may attach one OR several evidence links when opening a
    dispute — the full list reaches the dispute row verbatim."""
    service.order_repository.get_by_uuid_and_merchant.return_value = mock_order
    service.repository.get_by_order_id.return_value = None
    service.repository.create.return_value = MagicMock(spec=Dispute, id=200)

    links = ["https://cdn/proof1.png", "https://cdn/proof2.pdf", "uploads/x.jpg"]
    data = DisputeCreate(reason=DisputeReason.HAS_PAYMENT, evidence_files=links)

    await service.open_dispute_by_merchant(mock_merchant, data, order_id="some-uuid")

    created = service.repository.create.call_args.args[0]
    assert created["evidence_files"] == links


@pytest.mark.asyncio
async def test_open_dispute_defaults_evidence_to_empty_list(
    service, mock_merchant, mock_order, fake_finance, stub_celery, stub_change_status
):
    """Omitting evidence_files is fine — it defaults to an empty list, not None."""
    service.order_repository.get_by_uuid_and_merchant.return_value = mock_order
    service.repository.get_by_order_id.return_value = None
    service.repository.create.return_value = MagicMock(spec=Dispute, id=201)

    data = DisputeCreate(reason=DisputeReason.NO_PAYMENT)  # no evidence_files

    await service.open_dispute_by_merchant(mock_merchant, data, order_id="some-uuid")

    created = service.repository.create.call_args.args[0]
    assert created["evidence_files"] == []


# ── evidence files: uploaded + downloaded-from-link, via ReceiptService ─────

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def _patch_receipt_service(mocker, *, stored_paths=None, upload=None):
    """Patch ReceiptService used inside open_dispute_by_merchant; return its
    upload mock so callers can assert the attach calls."""
    upload = upload or AsyncMock()
    svc = MagicMock(
        upload=upload,
        list_for_dispute=AsyncMock(return_value=[
            MagicMock(file_path=p) for p in (stored_paths or [])
        ]),
    )
    mocker.patch("app.modules.receipts.service.ReceiptService", MagicMock(return_value=svc))
    return upload


@pytest.mark.asyncio
async def test_open_dispute_attaches_uploaded_file_as_receipt(
    service, mock_merchant, mock_order, fake_finance, stub_celery, mocker, stub_change_status
):
    service.order_repository.get_by_uuid_and_merchant.return_value = mock_order
    service.repository.get_by_order_id.return_value = None
    service.repository.create.return_value = MagicMock(spec=Dispute, id=300)
    service.order_repository.get.return_value = mock_order  # reloaded DISPUTED order

    upload = _patch_receipt_service(mocker, stored_paths=["uploads/receipts/x.png"])

    data = DisputeCreate(reason=DisputeReason.NO_PAYMENT)
    await service.open_dispute_by_merchant(
        mock_merchant, data, order_id="u", attachments=[(_PNG, "proof.png")]
    )

    upload.assert_awaited_once()
    kw = upload.await_args.kwargs
    assert kw["dispute_id"] == 300
    assert kw["content"] == _PNG and kw["filename"] == "proof.png"
    assert kw["uploaded_by"] == "merchant"
    # dispute.evidence_files repopulated with the trusted stored receipt path.
    upd = service.repository.update.call_args
    assert upd.args[0] == 300
    assert upd.args[1]["evidence_files"] == ["uploads/receipts/x.png"]


@pytest.mark.asyncio
async def test_open_dispute_downloads_link_and_attaches(
    service, mock_merchant, mock_order, fake_finance, stub_celery, mocker, stub_change_status
):
    from app.modules.receipts.download import DownloadedEvidence

    service.order_repository.get_by_uuid_and_merchant.return_value = mock_order
    service.repository.get_by_order_id.return_value = None
    service.repository.create.return_value = MagicMock(spec=Dispute, id=301)
    service.order_repository.get.return_value = mock_order

    fetch = AsyncMock(return_value=DownloadedEvidence(content=_PNG, filename="dl.png", mime="image/png"))
    mocker.patch("app.modules.receipts.download.fetch_evidence", fetch)
    upload = _patch_receipt_service(mocker)

    data = DisputeCreate(reason=DisputeReason.NO_PAYMENT)
    await service.open_dispute_by_merchant(
        mock_merchant, data, order_id="u", evidence_urls=["https://cdn.example/x.png"]
    )

    fetch.assert_awaited_once_with("https://cdn.example/x.png")
    upload.assert_awaited_once()
    assert upload.await_args.kwargs["content"] == _PNG
    assert upload.await_args.kwargs["filename"] == "dl.png"


@pytest.mark.asyncio
async def test_open_dispute_rejects_bad_format_before_creating(
    service, mock_merchant, mock_order, fake_finance, stub_celery
):
    """sync-strict: a bad-format upload fails the request BEFORE the dispute is
    created (no partial state)."""
    service.order_repository.get_by_uuid_and_merchant.return_value = mock_order
    service.repository.get_by_order_id.return_value = None

    data = DisputeCreate(reason=DisputeReason.NO_PAYMENT)
    with pytest.raises(ValidationException):
        await service.open_dispute_by_merchant(
            mock_merchant, data, order_id="u",
            attachments=[(b"not an image", "proof.png")],  # bad magic
        )
    service.repository.create.assert_not_called()


@pytest.mark.asyncio
async def test_open_dispute_rejects_bad_link_before_creating(
    service, mock_merchant, mock_order, fake_finance, stub_celery, mocker
):
    service.order_repository.get_by_uuid_and_merchant.return_value = mock_order
    service.repository.get_by_order_id.return_value = None
    mocker.patch(
        "app.modules.receipts.download.fetch_evidence",
        AsyncMock(side_effect=ValidationException("unsafe url")),
    )

    data = DisputeCreate(reason=DisputeReason.NO_PAYMENT)
    with pytest.raises(ValidationException):
        await service.open_dispute_by_merchant(
            mock_merchant, data, order_id="u", evidence_urls=["http://169.254.169.254/x.png"]
        )
    service.repository.create.assert_not_called()


# ── open_dispute_by_admin: admin opens a dispute on any order (by UUID) ─────


@pytest.mark.asyncio
async def test_open_dispute_by_admin_success(
    service, mock_session, mock_order, fake_finance, stub_celery, stub_change_status
):
    mock_order.merchant_id = 3
    service.order_repository.get_by_uuid.return_value = mock_order
    service.repository.get_by_order_id.return_value = None
    service.order_repository.get.return_value = mock_order
    service.repository.create.return_value = MagicMock(spec=Dispute, id=400)
    # session.get(Merchant, ...) / session.get(User, ...) both resolve here.
    mock_session.get = AsyncMock(return_value=MagicMock(id=3, user_id=99))

    dispute = await service.open_dispute_by_admin(
        admin_id=42, reason=DisputeReason.NO_PAYMENT, order_uuid="order-uuid",
    )

    assert dispute.id == 400
    service.order_repository.get_by_uuid.assert_awaited_once_with("order-uuid")
    created = service.repository.create.call_args.args[0]
    assert created["initiator_type"] == UserRole.ADMIN
    assert created["initiator_id"] == 42
    assert created["merchant_id"] == 3
    stub_change_status.assert_awaited_once()
    assert stub_change_status.await_args.args[1] == OrderStatus.DISPUTED
    # Audited under the admin's id.
    assert service.audit_log.call_args.kwargs["user_id"] == 42


@pytest.mark.asyncio
async def test_open_dispute_by_admin_order_not_found(service):
    service.order_repository.get_by_uuid.return_value = None
    with pytest.raises(NotFoundException):
        await service.open_dispute_by_admin(
            admin_id=1, reason=DisputeReason.UNKNOWN, order_uuid="missing"
        )
    service.repository.create.assert_not_called()


@pytest.mark.asyncio
async def test_open_dispute_by_admin_rejects_order_without_trader(service, mock_order):
    mock_order.trader_id = None
    service.order_repository.get_by_uuid.return_value = mock_order
    with pytest.raises(ValidationException):
        await service.open_dispute_by_admin(
            admin_id=1, reason=DisputeReason.UNKNOWN, order_uuid="u"
        )
    service.repository.create.assert_not_called()


@pytest.mark.asyncio
async def test_open_dispute_by_admin_attaches_evidence_as_system(
    service, mock_session, mock_order, fake_finance, stub_celery, mocker, stub_change_status
):
    mock_order.merchant_id = 3
    service.order_repository.get_by_uuid.return_value = mock_order
    service.repository.get_by_order_id.return_value = None
    service.order_repository.get.return_value = mock_order
    service.repository.create.return_value = MagicMock(spec=Dispute, id=401)
    mock_session.get = AsyncMock(return_value=MagicMock(id=3, user_id=99))
    upload = _patch_receipt_service(mocker, stored_paths=["uploads/receipts/x.png"])

    await service.open_dispute_by_admin(
        admin_id=7, reason=DisputeReason.HAS_PAYMENT, order_uuid="u",
        attachments=[(_PNG, "proof.png")],
    )

    upload.assert_awaited_once()
    kw = upload.await_args.kwargs
    assert kw["uploaded_by"] == "system" and kw["dispute_id"] == 401


@pytest.mark.asyncio
async def test_open_dispute_by_merchant_success_external_id(
    service, mock_merchant, mock_order, fake_finance, stub_change_status
):
    service.order_repository.get_by_external_id_and_merchant.return_value = mock_order
    service.repository.get_by_order_id.return_value = None

    mock_dispute = MagicMock(spec=Dispute)
    mock_dispute.id = 101
    service.repository.create.return_value = mock_dispute

    data = DisputeCreate(reason=DisputeReason.NO_PAYMENT)

    result = await service.open_dispute_by_merchant(mock_merchant, data, external_id="ext-123")

    assert result == mock_dispute
    service.order_repository.get_by_external_id_and_merchant.assert_called_once_with("ext-123", mock_merchant.id)
    stub_change_status.assert_awaited_once()
    assert stub_change_status.await_args.args[1] == OrderStatus.DISPUTED


@pytest.mark.asyncio
async def test_open_dispute_on_success_does_not_touch_rewards_or_turnover(
    service, mock_merchant, fake_finance, fake_teamlead, fake_requisite_limits, stub_change_status
):
    """Opening a dispute on a SUCCESS order normalises the escrow (reconcile) but
    must NOT touch teamlead rewards or requisite turnover — those are adjusted
    only when the dispute is decided (resolve keeps them / reject undoes them)."""
    order = MagicMock(spec=Order)
    order.id = 10
    order.status = OrderStatus.SUCCESS
    order.trader_id = 20
    service.order_repository.get_by_uuid_and_merchant.return_value = order
    service.repository.get_by_order_id.return_value = None

    mock_dispute = MagicMock(spec=Dispute)
    mock_dispute.id = 102
    service.repository.create.return_value = mock_dispute

    data = DisputeCreate(reason=DisputeReason.INVALID_SUM)

    await service.open_dispute_by_merchant(mock_merchant, data, order_id="u")

    # The order→DISPUTED transition (+ escrow reconcile) is delegated to the
    # funnel; opening must NOT reverse rewards or decrement turnover — those
    # move only when the dispute is decided.
    stub_change_status.assert_awaited_once()
    assert stub_change_status.await_args.args[1] == OrderStatus.DISPUTED
    fake_teamlead.reverse_rewards.assert_not_awaited()
    fake_requisite_limits.decrement_turnover_by_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_open_dispute_by_merchant_missing_ids(service, mock_merchant):
    data = DisputeCreate(reason=DisputeReason.UNKNOWN)

    with pytest.raises(ValidationException, match="Either order_id or external_id must be provided"):
        await service.open_dispute_by_merchant(mock_merchant, data)


@pytest.mark.asyncio
async def test_open_dispute_by_merchant_order_not_found(service, mock_merchant):
    service.order_repository.get_by_uuid_and_merchant.return_value = None
    data = DisputeCreate(reason=DisputeReason.UNKNOWN)

    with pytest.raises(NotFoundException, match="Order some-uuid not found"):
        await service.open_dispute_by_merchant(mock_merchant, data, order_id="some-uuid")


@pytest.mark.asyncio
async def test_open_dispute_allows_active_pending(
    service, mock_merchant, mock_order, fake_finance, stub_celery, stub_change_status
):
    """PENDING (active, collateral already in ESCROW) is now disputable —
    e.g. premoderation pulls an in-flight order into a dispute. reconcile is
    called with pre_status=PENDING (its no-op branch — escrow untouched)."""
    mock_order.status = OrderStatus.PENDING
    service.order_repository.get_by_uuid_and_merchant.return_value = mock_order
    service.repository.get_by_order_id.return_value = None
    mock_dispute = MagicMock(spec=Dispute)
    mock_dispute.id = 101
    service.repository.create.return_value = mock_dispute
    data = DisputeCreate(reason=DisputeReason.UNKNOWN)

    result = await service.open_dispute_by_merchant(mock_merchant, data, order_id="some-uuid")

    assert result == mock_dispute
    stub_change_status.assert_awaited_once()
    assert stub_change_status.await_args.args[1] == OrderStatus.DISPUTED


@pytest.mark.asyncio
async def test_open_dispute_allows_active_receipt_uploaded(
    service, mock_merchant, mock_order, fake_finance, stub_celery, stub_change_status
):
    """RECEIPT_UPLOADED (active) is disputable too — money stays frozen in
    ESCROW (reconcile no-op), order waits for the dispute decision."""
    mock_order.status = OrderStatus.RECEIPT_UPLOADED
    service.order_repository.get_by_uuid_and_merchant.return_value = mock_order
    service.repository.get_by_order_id.return_value = None
    mock_dispute = MagicMock(spec=Dispute)
    mock_dispute.id = 102
    service.repository.create.return_value = mock_dispute
    data = DisputeCreate(reason=DisputeReason.UNKNOWN)

    result = await service.open_dispute_by_merchant(mock_merchant, data, order_id="some-uuid")

    assert result == mock_dispute
    stub_change_status.assert_awaited_once()
    assert stub_change_status.await_args.args[1] == OrderStatus.DISPUTED


@pytest.mark.asyncio
async def test_open_dispute_rejects_order_without_trader(
    service, mock_merchant, mock_order, fake_finance
):
    """An order that never reached a trader (no requisite issued) is NOT
    disputable — opening would create a zombie that resolve/reject can't decide.
    The status is final/disputable here, so it's the trader check that rejects."""
    mock_order.status = OrderStatus.CANCELED
    mock_order.trader_id = None
    service.order_repository.get_by_uuid_and_merchant.return_value = mock_order
    service.repository.get_by_order_id.return_value = None
    data = DisputeCreate(reason=DisputeReason.NO_PAYMENT)

    with pytest.raises(ValidationException, match="never assigned to a trader"):
        await service.open_dispute_by_merchant(mock_merchant, data, order_id="some-uuid")

    # Nothing was created / reconciled — we bailed before any side effects.
    service.repository.create.assert_not_called()
    fake_finance.reconcile_for_dispute.assert_not_awaited()


@pytest.mark.asyncio
async def test_open_dispute_swallows_cascade_broker_errors(
    service, mock_merchant, mock_order, fake_finance, mocker, stub_change_status
):
    """A merchant opening a dispute must NOT get a 500 if the broker is down
    when the best-effort cascade-forward / webhook tasks are enqueued."""
    service.order_repository.get_by_uuid_and_merchant.return_value = mock_order
    service.repository.get_by_order_id.return_value = None

    mock_dispute = MagicMock(spec=Dispute)
    mock_dispute.id = 100
    service.repository.create.return_value = mock_dispute

    fake_celery = MagicMock()
    fake_celery.send_task = MagicMock(side_effect=RuntimeError("broker down"))
    mocker.patch("app.workers.celery_app.celery_app", fake_celery)

    data = DisputeCreate(reason=DisputeReason.UNKNOWN)

    # Must NOT raise — broker errors are swallowed.
    result = await service.open_dispute_by_merchant(mock_merchant, data, order_id="some-uuid")
    assert result is mock_dispute
    # Both best-effort enqueues were attempted (cascade forward + webhook).
    names = [c.args[0] for c in fake_celery.send_task.call_args_list]
    assert any("cascade.forward_dispute_to_provider" in n for n in names)
    assert any("callbacks.send_order_callback" in n for n in names)


@pytest.mark.asyncio
async def test_open_dispute_by_merchant_existing_open_appends_evidence(
    service, mock_merchant, mock_order, mocker
):
    """A 2nd create-dispute call when an OPEN dispute already exists APPENDS the
    new check to it (idempotent-additive) — no new dispute, no 'already exists'
    error. The collected evidence is routed straight to the re-attach helper."""
    service.order_repository.get_by_uuid_and_merchant.return_value = mock_order
    existing = MagicMock(spec=Dispute)
    existing.status = DisputeStatus.OPEN
    service.repository.get_by_order_id.return_value = existing
    mocker.patch.object(
        service, "_collect_dispute_evidence", AsyncMock(return_value=[(b"x", "r.pdf")])
    )
    attach = mocker.patch.object(service, "_attach_evidence_to_dispute", AsyncMock())
    data = DisputeCreate(reason=DisputeReason.UNKNOWN)

    result = await service.open_dispute_by_merchant(mock_merchant, data, order_id="some-uuid")

    assert result is existing                       # returns the existing dispute
    service.repository.create.assert_not_called()   # no new dispute opened
    attach.assert_awaited_once()
    kw = attach.await_args.kwargs
    assert kw["dispute"] is existing
    assert kw["evidence_to_store"] == [(b"x", "r.pdf")]
    assert kw["uploaded_by"] == ReceiptUploader.MERCHANT


@pytest.mark.asyncio
async def test_open_dispute_by_merchant_existing_closed_rejected(service, mock_merchant, mock_order):
    """Re-attach to a CLOSED dispute is rejected — it's already decided."""
    service.order_repository.get_by_uuid_and_merchant.return_value = mock_order
    existing = MagicMock(spec=Dispute)
    existing.status = DisputeStatus.RESOLVED
    service.repository.get_by_order_id.return_value = existing
    data = DisputeCreate(reason=DisputeReason.UNKNOWN)

    with pytest.raises(ValidationException, match="already closed"):
        await service.open_dispute_by_merchant(mock_merchant, data, order_id="some-uuid")


# ────────────────────────────────────────────────────────────────
# merchant getters
# ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_merchant_dispute_success(service, mock_merchant):
    mock_dispute = MagicMock(spec=Dispute)
    service.repository.get_by_uuid_and_merchant.return_value = mock_dispute

    result = await service.get_merchant_dispute(mock_merchant, "dispute-uuid")

    assert result == mock_dispute
    service.repository.get_by_uuid_and_merchant.assert_called_once_with("dispute-uuid", mock_merchant.id)


@pytest.mark.asyncio
async def test_get_merchant_dispute_not_found(service, mock_merchant):
    service.repository.get_by_uuid_and_merchant.return_value = None

    with pytest.raises(NotFoundException, match="Dispute dispute-uuid not found"):
        await service.get_merchant_dispute(mock_merchant, "dispute-uuid")


# ── Merchant adds another receipt to an OPEN dispute (post-open re-upload) ─────

@pytest.mark.asyncio
async def test_add_merchant_evidence_attaches_to_open_dispute(service, mock_merchant, mocker):
    """Merchant re-uploads a check to their open dispute → routed through the
    canonical confirm_order path (linked to the dispute, premoderation runs),
    same as the dispute-bot, with the channel-specific uploader."""
    dispute = MagicMock(spec=Dispute)
    dispute.status = DisputeStatus.OPEN
    dispute.id = 100
    dispute.order_id = 7
    service.repository.get_by_uuid_and_merchant.return_value = dispute
    order = MagicMock(spec=Order); order.id = 7; order.uuid = "order-uuid-1"
    service.order_repository.get.return_value = order
    cs = mocker.patch(
        "app.modules.orders.service.OrderService.confirm_order",
        AsyncMock(return_value=order),
    )
    mirror = mocker.patch.object(service, "_mirror_dispute_evidence_files", AsyncMock())
    detail = MagicMock()
    mocker.patch.object(service, "get_merchant_dispute_detail", AsyncMock(return_value=detail))

    result = await service.add_merchant_evidence(
        mock_merchant, "abc", MagicMock(), uploaded_by=ReceiptUploader.MERCHANT_WEB,
    )

    assert result is detail
    cs.assert_awaited_once()
    kw = cs.await_args.kwargs
    assert kw["merchant"] is mock_merchant
    assert kw["order_id"] == "order-uuid-1"        # confirm_order resolves by uuid
    assert kw["dispute_id"] == 100                  # receipt linked to the appeal
    assert kw["uploaded_by"] == ReceiptUploader.MERCHANT_WEB
    # evidence_files is re-mirrored so evidence_count reflects the new receipt
    # (confirm_order links the receipt but doesn't touch dispute.evidence_files).
    mirror.assert_awaited_once_with(100)


@pytest.mark.asyncio
async def test_add_merchant_evidence_rejects_closed_dispute(service, mock_merchant, mocker):
    dispute = MagicMock(spec=Dispute); dispute.status = DisputeStatus.RESOLVED
    service.repository.get_by_uuid_and_merchant.return_value = dispute
    cs = mocker.patch("app.modules.orders.service.OrderService.confirm_order", AsyncMock())

    with pytest.raises(ValidationException, match="already closed"):
        await service.add_merchant_evidence(
            mock_merchant, "abc", MagicMock(), uploaded_by=ReceiptUploader.MERCHANT,
        )

    cs.assert_not_awaited()  # no receipt path for a closed dispute


@pytest.mark.asyncio
async def test_add_merchant_evidence_order_missing_raises_not_found(service, mock_merchant, mocker):
    """Open dispute but the order can't be resolved → NotFound, no upload."""
    dispute = MagicMock(spec=Dispute); dispute.status = DisputeStatus.OPEN; dispute.order_id = 7
    service.repository.get_by_uuid_and_merchant.return_value = dispute
    service.order_repository.get.return_value = None
    cs = mocker.patch("app.modules.orders.service.OrderService.confirm_order", AsyncMock())

    with pytest.raises(NotFoundException, match="Order for dispute"):
        await service.add_merchant_evidence(
            mock_merchant, "abc", MagicMock(), uploaded_by=ReceiptUploader.MERCHANT,
        )

    cs.assert_not_awaited()


# ────────────────────────────────────────────────────────────────
# resolve_dispute — merchant wins. Flat: always the ordinary completion
# flow, regardless of the pre-dispute status (money is frozen in trader
# ESCROW by reconcile_for_dispute on open).
# ────────────────────────────────────────────────────────────────


def _wire_dispute_order(service, mock_session, *, pre_status):
    dispute = MagicMock(spec=Dispute)
    dispute.status = DisputeStatus.OPEN
    dispute.id = 100
    dispute.order_id = 7

    order = MagicMock(spec=Order)
    order.id = 7
    order.merchant_id = 1
    order.trader_id = 5
    service.repository.get.return_value = dispute
    service.repository.update.return_value = dispute
    service.order_repository.get.return_value = order
    service.order_repository.update.return_value = order

    trader = MagicMock(); trader.id = 5
    mock_session.get.return_value = trader
    return dispute, order


@pytest.mark.asyncio
@pytest.mark.parametrize("pre_status", [OrderStatus.SUCCESS, OrderStatus.FAILED, OrderStatus.CANCELED])
async def test_resolve_dispute_completes_and_counts_only_if_not_already_success(
    service, mock_session, stub_celery, mocker, pre_status
):
    """Resolve marks the dispute RESOLVED and delegates the order → SUCCESS
    transition to OrderService.change_status, which re-settles everything fresh
    (opening the dispute already reversed any prior settlement). The escrow math
    itself is verified by the dispute money-flow integration tests."""
    dispute, order = _wire_dispute_order(service, mock_session, pre_status=pre_status)
    cs = mocker.patch(
        "app.modules.orders.service.OrderService.change_status",
        AsyncMock(return_value=order),
    )

    result = await service.resolve_dispute(admin_id=42, dispute_id=100, resolution_text="ok")

    assert result is dispute
    cs.assert_awaited_once()
    assert cs.await_args.args[1] == OrderStatus.SUCCESS
    dispute_update = service.repository.update.call_args[0][1]
    assert dispute_update["status"] == DisputeStatus.RESOLVED
    assert order.id in _callback_order_ids(stub_celery)


@pytest.mark.asyncio
async def test_resolve_dispute_closed_rejected(service):
    dispute = MagicMock(spec=Dispute)
    dispute.status = DisputeStatus.RESOLVED
    service.repository.get.return_value = dispute
    service.repository.lock_status.return_value = DisputeStatus.RESOLVED

    with pytest.raises(ValidationException, match="already closed"):
        await service.resolve_dispute(admin_id=42, dispute_id=100, resolution_text="hi")


@pytest.mark.asyncio
async def test_resolve_dispute_no_trader_rejected(service):
    dispute = MagicMock(spec=Dispute)
    dispute.status = DisputeStatus.OPEN
    dispute.id = 100
    dispute.order_id = 7

    order = MagicMock(spec=Order); order.id = 7
    order.merchant_id = 1
    order.trader_id = None
    service.repository.get.return_value = dispute
    service.order_repository.get.return_value = order

    with pytest.raises(ValidationException, match="no assigned trader"):
        await service.resolve_dispute(admin_id=42, dispute_id=100, resolution_text="hi")


# ────────────────────────────────────────────────────────────────
# reject_dispute — trader wins. Flat: always cancel_order, order → FAILED,
# for every prior status.
# ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("pre_status", [OrderStatus.SUCCESS, OrderStatus.FAILED, OrderStatus.CANCELED])
async def test_reject_dispute_delegates_to_failed(
    service, mock_session, stub_celery, mocker, pre_status
):
    """Reject marks the dispute REJECTED and delegates order → FAILED to
    change_status (escrow released to the trader). Teamlead/turnover reversal is
    NOT done here — opening the dispute already reversed any prior settlement via
    reconcile_for_dispute (verified by the dispute money-flow integration tests)."""
    dispute, order = _wire_dispute_order(service, mock_session, pre_status=pre_status)
    cs = mocker.patch(
        "app.modules.orders.service.OrderService.change_status",
        AsyncMock(return_value=order),
    )

    await service.reject_dispute(admin_id=42, dispute_id=100, resolution_text="no")

    cs.assert_awaited_once()
    assert cs.await_args.args[1] == OrderStatus.FAILED
    dispute_update = service.repository.update.call_args[0][1]
    assert dispute_update["status"] == DisputeStatus.REJECTED
    assert order.id in _callback_order_ids(stub_celery)


@pytest.mark.asyncio
async def test_reject_dispute_closed_rejected(service):
    dispute = MagicMock(spec=Dispute)
    dispute.status = DisputeStatus.REJECTED
    service.repository.get.return_value = dispute
    service.repository.lock_status.return_value = DisputeStatus.REJECTED

    with pytest.raises(ValidationException, match="already closed"):
        await service.reject_dispute(admin_id=42, dispute_id=100, resolution_text="hi")


@pytest.mark.asyncio
async def test_reject_dispute_no_trader_rejected(service):
    dispute = MagicMock(spec=Dispute)
    dispute.status = DisputeStatus.OPEN
    dispute.order_id = 7

    order = MagicMock(spec=Order); order.merchant_id = 1; order.trader_id = None
    service.repository.get.return_value = dispute
    service.order_repository.get.return_value = order

    with pytest.raises(ValidationException, match="no assigned trader"):
        await service.reject_dispute(admin_id=42, dispute_id=100, resolution_text="reject")


# ────────────────────────────────────────────────────────────────
# Trader-facing list & detail.
# ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_trader_disputes_empty(service):
    service.repository.list_for_trader.return_value = []

    result = await service.list_trader_disputes(trader_id=5)

    assert result == []


def _make_validatable_dispute(*, dispute_id=100, order_id=7, status=DisputeStatus.OPEN):
    """Build a Dispute-shaped object that DisputeResponse.model_validate accepts."""
    from uuid import uuid4
    from datetime import datetime as _dt
    return Dispute(
        id=dispute_id,
        uuid=uuid4(),
        order_id=order_id,
        merchant_id=1,
        reason=DisputeReason.UNKNOWN,
        evidence_files=[],
        initiator_type=UserRole.MERCHANT,
        initiator_id=1,
        status=status,
        assigned_user_type=UserRole.ADMIN,
        assigned_user_id=42,
        resolution_text=None,
        resolved_by_type=None,
        resolved_by_id=None,
        resolved_at=None,
        created_at=_dt(2026, 5, 1),
    )


@pytest.mark.asyncio
async def test_list_trader_disputes_enriches_with_order_info(service, mock_session):
    dispute = _make_validatable_dispute()
    service.repository.list_for_trader.return_value = [dispute]

    order = MagicMock(spec=Order)
    order.id = 7
    order.uuid = "order-uuid"
    order.external_id = "ext-1"
    order.amount = 1000
    order.payment_method = MagicMock(value="card")

    scalars = MagicMock()
    scalars.all.return_value = [order]
    result = MagicMock()
    result.scalars.return_value = scalars
    mock_session.execute.return_value = result

    out = await service.list_trader_disputes(trader_id=5)

    assert len(out) == 1
    assert out[0].order_uuid == "order-uuid"
    assert out[0].order_external_id == "ext-1"
    assert out[0].order_payment_method == "card"


@pytest.mark.asyncio
async def test_get_trader_dispute_success(service):
    dispute = MagicMock(spec=Dispute)
    service.repository.get_by_uuid_and_trader.return_value = dispute

    result = await service.get_trader_dispute(trader_id=5, dispute_uuid="abc")

    assert result is dispute
    service.repository.get_by_uuid_and_trader.assert_called_once_with("abc", 5)


@pytest.mark.asyncio
async def test_get_trader_dispute_not_found(service):
    service.repository.get_by_uuid_and_trader.return_value = None

    with pytest.raises(NotFoundException, match="Dispute abc not found"):
        await service.get_trader_dispute(trader_id=5, dispute_uuid="abc")


@pytest.mark.asyncio
async def test_get_trader_dispute_detail_enriches_order(service):
    """Trader detail returns the dispute enriched with order info (no comments)."""
    dispute = _make_validatable_dispute()
    service.repository.get_by_uuid_and_trader.return_value = dispute

    order = MagicMock(spec=Order)
    order.uuid = "ord-uuid"
    order.external_id = "ext-1"
    order.amount = 100
    order.payment_method = MagicMock(value="sbp")
    service.order_repository.get.return_value = order

    result = await service.get_trader_dispute_detail(trader_id=5, dispute_uuid="abc")

    assert result.order_uuid == "ord-uuid"
    assert result.order_external_id == "ext-1"
    assert result.order_payment_method == "sbp"


# ────────────────────────────────────────────────────────────────
# get_merchant_dispute_detail — dispute enriched with order info.
# ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_merchant_dispute_detail_enriches_order(service, mock_merchant):
    dispute = _make_validatable_dispute()
    service.repository.get_by_uuid_and_merchant.return_value = dispute

    order = MagicMock(spec=Order)
    order.uuid = "ord-uuid"
    order.external_id = "ext-1"
    service.order_repository.get.return_value = order

    result = await service.get_merchant_dispute_detail(mock_merchant, "abc")

    assert result.order_uuid == "ord-uuid"
    assert result.order_external_id == "ext-1"


# ────────────────────────────────────────────────────────────────
# Trader self-service: accept (concede → merchant favour) /
# reject (decide in own favour → FAILED) / request video|pdf (stays OPEN).
# ────────────────────────────────────────────────────────────────


def _wire_trader_dispute(service, mock_session, *, pre_status, status=DisputeStatus.OPEN):
    """Wire a trader-owned dispute (fetched via get_by_uuid_and_trader)."""
    dispute = MagicMock(spec=Dispute)
    dispute.status = status
    dispute.id = 100
    dispute.order_id = 7

    order = MagicMock(spec=Order)
    order.id = 7
    order.merchant_id = 1
    order.trader_id = 5

    service.repository.get_by_uuid_and_trader.return_value = dispute
    service.repository.update.return_value = dispute
    service.order_repository.get.return_value = order
    service.order_repository.update.return_value = order

    trader = MagicMock(); trader.id = 5
    mock_session.get.return_value = trader
    return dispute, order


@pytest.mark.asyncio
@pytest.mark.parametrize("pre_status", [OrderStatus.SUCCESS, OrderStatus.CANCELED])
async def test_trader_accept_dispute_resolves_in_merchant_favour(
    service, mock_session, stub_celery, mocker, pre_status
):
    """Trader 'accept' = concede → same path as admin resolve (delegates order →
    SUCCESS to change_status), but recorded as resolved_by TRADER."""
    dispute, order = _wire_trader_dispute(service, mock_session, pre_status=pre_status)
    cs = mocker.patch(
        "app.modules.orders.service.OrderService.change_status",
        AsyncMock(return_value=order),
    )

    result = await service.trader_accept_dispute(trader_id=5, dispute_uuid="abc")

    assert result is dispute
    cs.assert_awaited_once()
    assert cs.await_args.args[1] == OrderStatus.SUCCESS

    dispute_update = service.repository.update.call_args[0][1]
    assert dispute_update["status"] == DisputeStatus.RESOLVED
    assert dispute_update["resolved_by_type"] == UserRole.TRADER
    assert dispute_update["resolved_by_id"] == 5
    assert order.id in _callback_order_ids(stub_celery)


@pytest.mark.asyncio
async def test_trader_accept_closed_dispute_rejected(service):
    dispute = MagicMock(spec=Dispute)
    dispute.status = DisputeStatus.REJECTED
    service.repository.get_by_uuid_and_trader.return_value = dispute
    service.repository.lock_status.return_value = DisputeStatus.REJECTED

    with pytest.raises(ValidationException, match="already closed"):
        await service.trader_accept_dispute(trader_id=5, dispute_uuid="abc")


@pytest.mark.asyncio
async def test_trader_accept_not_own_dispute(service):
    service.repository.get_by_uuid_and_trader.return_value = None

    with pytest.raises(NotFoundException, match="Dispute abc not found"):
        await service.trader_accept_dispute(trader_id=5, dispute_uuid="abc")


@pytest.mark.asyncio
@pytest.mark.parametrize("pre_status", [OrderStatus.SUCCESS, OrderStatus.CANCELED])
async def test_trader_reject_dispute_fails_order(
    service, mock_session, stub_celery, mocker, pre_status
):
    """Trader 'reject' = decide in own favour → order FAILED (same money path as
    admin reject, delegating to change_status), recorded as rejected_by TRADER."""
    dispute, order = _wire_trader_dispute(service, mock_session, pre_status=pre_status)
    cs = mocker.patch(
        "app.modules.orders.service.OrderService.change_status",
        AsyncMock(return_value=order),
    )

    result = await service.trader_reject_dispute(trader_id=5, dispute_uuid="abc")

    assert result is dispute
    cs.assert_awaited_once()
    assert cs.await_args.args[1] == OrderStatus.FAILED
    dispute_update = service.repository.update.call_args[0][1]
    assert dispute_update["status"] == DisputeStatus.REJECTED
    assert dispute_update["resolved_by_type"] == UserRole.TRADER
    assert dispute_update["resolved_by_id"] == 5
    assert order.id in _callback_order_ids(stub_celery)


@pytest.mark.asyncio
async def test_trader_reject_closed_dispute_rejected(service):
    dispute = MagicMock(spec=Dispute)
    dispute.status = DisputeStatus.RESOLVED
    service.repository.get_by_uuid_and_trader.return_value = dispute
    service.repository.lock_status.return_value = DisputeStatus.RESOLVED

    with pytest.raises(ValidationException, match="already closed"):
        await service.trader_reject_dispute(trader_id=5, dispute_uuid="abc")


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,substatus_value", [("video", "video_requested"), ("pdf", "pdf_requested")])
async def test_trader_request_proof_sets_substatus_and_nudges_merchant(
    service, mock_session, fake_finance, stub_celery, mocker, kind, substatus_value
):
    """Trader 'request video/pdf' keeps the dispute OPEN, sets the substatus, and
    nudges the merchant for the proof — no money path, no order transition."""
    from app.common.enums.disputes import DisputeSubstatus
    dispute, order = _wire_trader_dispute(service, mock_session, pre_status=OrderStatus.SUCCESS)
    nudge = mocker.patch("app.modules.receipts.effects.enqueue_merchant_proof_request")

    result = await service.trader_request_proof(trader_id=5, dispute_uuid="abc", kind=kind)

    assert result is dispute
    update_kwargs = service.repository.update.call_args[0][1]
    assert update_kwargs["substatus"] == DisputeSubstatus(substatus_value)
    assert "status" not in update_kwargs  # stays OPEN
    # No money path, no order transition.
    fake_finance.complete_order.assert_not_awaited()
    fake_finance.cancel_order.assert_not_awaited()
    service.order_repository.update.assert_not_called()
    # Merchant nudged for the matching proof (order_id, "request_video"|"request_pdf").
    nudge.assert_called_once()
    assert nudge.call_args.args[0] == dispute.order_id
    assert nudge.call_args.args[1] == f"request_{kind}"
    # ...and the order webhook fires too, so the merchant sees the proof request
    # in the callback (its dispute.substatus carries pdf_requested/video_requested).
    assert dispute.order_id in _callback_order_ids(stub_celery)


@pytest.mark.asyncio
async def test_trader_request_proof_invalid_kind(service):
    with pytest.raises(ValidationException, match="Unsupported proof kind"):
        await service.trader_request_proof(trader_id=5, dispute_uuid="abc", kind="audio")


@pytest.mark.asyncio
async def test_trader_request_proof_closed_dispute_rejected(service):
    dispute = MagicMock(spec=Dispute)
    dispute.status = DisputeStatus.RESOLVED
    service.repository.get_by_uuid_and_trader.return_value = dispute
    service.repository.lock_status.return_value = DisputeStatus.RESOLVED

    with pytest.raises(ValidationException, match="already closed"):
        await service.trader_request_proof(trader_id=5, dispute_uuid="abc", kind="video")


# ────────────────────────────────────────────────────────────────
# list_merchant_disputes / list_admin — pure list pass-throughs.
# ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_merchant_disputes_passthrough(service):
    service.repository.list_for_merchant.return_value = ["d1", "d2"]
    result = await service.list_merchant_disputes(merchant_id=1, skip=0, limit=10)
    assert result == ["d1", "d2"]
    service.repository.list_for_merchant.assert_called_once_with(1, skip=0, limit=10)


@pytest.mark.asyncio
async def test_list_admin_empty_short_circuits(service):
    service.repository.list_admin.return_value = []
    result = await service.list_admin()
    assert result == []


# ────────────────────────────────────────────────────────────────
# open_dispute_from_premoderation — premoderation pulls an active
# order into a dispute (admin asked merchant for PDF / video).
# ────────────────────────────────────────────────────────────────


def _premod_order(status=OrderStatus.RECEIPT_UPLOADED, trader_id=20):
    order = MagicMock(spec=Order)
    order.id = 55
    order.merchant_id = 7
    order.status = status
    order.trader_id = trader_id
    return order


@pytest.mark.asyncio
@pytest.mark.parametrize("decision_value,expected_substatus", [
    ("request_pdf", "pdf_requested"),
    ("request_video", "video_requested"),
])
async def test_open_dispute_from_premoderation_creates_with_substatus(
    service, fake_finance, stub_change_status, decision_value, expected_substatus,
):
    from app.common.enums.receipt_moderations import ModerationDecision
    from app.common.enums.disputes import DisputeReason, DisputeSubstatus

    order = _premod_order()
    service.repository.get_by_order_id.return_value = None
    mock_dispute = MagicMock(spec=Dispute)
    mock_dispute.id = 900
    service.repository.create.return_value = mock_dispute

    result = await service.open_dispute_from_premoderation(
        order=order, decision=ModerationDecision(decision_value),
        moderator_user_id=42,
    )

    assert result is mock_dispute
    # Dispute created with ADMIN initiator + check_suspended reason + substatus.
    created = service.repository.create.call_args[0][0]
    assert created["initiator_type"] == UserRole.ADMIN
    assert created["initiator_id"] == 42
    assert created["reason"] == DisputeReason.CHECK_SUSPENDED
    assert created["substatus"] == DisputeSubstatus(expected_substatus)
    # Order → DISPUTED delegated to the status funnel (escrow reconcile lives
    # there; for an active pre-status it's a no-op).
    stub_change_status.assert_awaited_once()
    assert stub_change_status.await_args.args[1] == OrderStatus.DISPUTED


@pytest.mark.asyncio
async def test_open_dispute_from_premoderation_accept_returns_none(service, fake_finance):
    """ACCEPT is not a proof request → no dispute opened."""
    from app.common.enums.receipt_moderations import ModerationDecision

    order = _premod_order()
    result = await service.open_dispute_from_premoderation(
        order=order, decision=ModerationDecision.ACCEPT,
    )
    assert result is None
    service.repository.create.assert_not_called()
    fake_finance.reconcile_for_dispute.assert_not_awaited()


@pytest.mark.asyncio
async def test_open_dispute_from_premoderation_no_trader_returns_none(service, fake_finance):
    """Order without a trader can't be disputed (resolve/reject need one)."""
    from app.common.enums.receipt_moderations import ModerationDecision

    order = _premod_order(trader_id=None)
    result = await service.open_dispute_from_premoderation(
        order=order, decision=ModerationDecision.REQUEST_PDF,
    )
    assert result is None
    service.repository.create.assert_not_called()


@pytest.mark.asyncio
async def test_open_dispute_from_premoderation_idempotent(service, fake_finance):
    """A re-fired premoderation callback with the SAME substatus on an OPEN
    dispute returns the existing one unchanged — no second dispute, no update."""
    from app.common.enums.receipt_moderations import ModerationDecision
    from app.common.enums.disputes import DisputeSubstatus

    order = _premod_order()
    existing = MagicMock(spec=Dispute)
    existing.id = 111
    existing.status = DisputeStatus.OPEN
    existing.substatus = DisputeSubstatus.PDF_REQUESTED
    service.repository.get_by_order_id.return_value = existing

    result = await service.open_dispute_from_premoderation(
        order=order, decision=ModerationDecision.REQUEST_PDF,
    )

    assert result is existing
    service.repository.create.assert_not_called()
    service.repository.update.assert_not_called()
    service.order_repository.update.assert_not_called()
    fake_finance.reconcile_for_dispute.assert_not_awaited()


@pytest.mark.asyncio
async def test_open_dispute_from_premoderation_refreshes_substatus_on_open(service):
    """A NEW premoderation request (VIDEO after PDF) on an OPEN dispute refreshes
    its substatus instead of silently dropping the request."""
    from app.common.enums.receipt_moderations import ModerationDecision
    from app.common.enums.disputes import DisputeSubstatus

    order = _premod_order()
    existing = MagicMock(spec=Dispute)
    existing.id = 111
    existing.status = DisputeStatus.OPEN
    existing.substatus = DisputeSubstatus.PDF_REQUESTED
    service.repository.get_by_order_id.return_value = existing
    service.repository.update.return_value = existing

    result = await service.open_dispute_from_premoderation(
        order=order, decision=ModerationDecision.REQUEST_VIDEO, moderator_user_id=42,
    )

    assert result is existing
    service.repository.create.assert_not_called()
    update_kwargs = service.repository.update.call_args[0][1]
    assert update_kwargs["substatus"] == DisputeSubstatus.VIDEO_REQUESTED


@pytest.mark.asyncio
async def test_open_dispute_from_premoderation_skips_closed_dispute(service):
    """A late premoderation decision on an ALREADY-CLOSED dispute must NOT revive
    it — returns None so the caller skips the merchant proof nudge."""
    from app.common.enums.receipt_moderations import ModerationDecision

    order = _premod_order()
    existing = MagicMock(spec=Dispute)
    existing.id = 111
    existing.status = DisputeStatus.RESOLVED
    service.repository.get_by_order_id.return_value = existing

    result = await service.open_dispute_from_premoderation(
        order=order, decision=ModerationDecision.REQUEST_PDF, moderator_user_id=42,
    )

    assert result is None
    service.repository.create.assert_not_called()
    service.repository.update.assert_not_called()


# ── trader-bot proof request (request_proof_from_trader_group) ──────────

@pytest.mark.asyncio
async def test_request_proof_from_trader_group_bad_kind(service):
    with pytest.raises(ValidationException, match="proof kind"):
        await service.request_proof_from_trader_group("uuid", 111, "audio")


@pytest.mark.asyncio
async def test_request_proof_from_trader_group_opens_check_suspended_and_notifies(service, mocker):
    """Trader taps «Запросить видео» → order group-authorised, check_suspended
    dispute opened with initiator=TRADER, and the merchant proof-request fan-out
    is enqueued with the decision."""
    from app.common.enums.receipt_moderations import ModerationDecision

    order = MagicMock(id=7, trader_id=99)
    OS = mocker.patch("app.modules.orders.service.OrderService")
    OS.return_value.get_order_for_trader_group = AsyncMock(return_value=order)
    dispute = MagicMock(spec=Dispute)
    service.open_dispute_from_premoderation = AsyncMock(return_value=dispute)
    enq = mocker.patch("app.modules.receipts.effects.enqueue_merchant_proof_request")

    result = await service.request_proof_from_trader_group("uuid-1", 111, "video")

    assert result is dispute
    OS.return_value.get_order_for_trader_group.assert_awaited_once_with("uuid-1", 111)
    assert service.open_dispute_from_premoderation.call_args.args[1] == ModerationDecision.REQUEST_VIDEO
    assert service.open_dispute_from_premoderation.call_args.kwargs["initiator_type"] == UserRole.TRADER
    assert service.open_dispute_from_premoderation.call_args.kwargs["initiator_id"] == 99
    enq.assert_called_once_with(7, ModerationDecision.REQUEST_VIDEO.value)


@pytest.mark.asyncio
async def test_request_proof_from_trader_group_raises_when_no_dispute(service, mocker):
    """Order can't be disputed (open returns None) → validation error and NO
    merchant notification enqueued."""
    order = MagicMock(id=7, trader_id=99)
    OS = mocker.patch("app.modules.orders.service.OrderService")
    OS.return_value.get_order_for_trader_group = AsyncMock(return_value=order)
    service.open_dispute_from_premoderation = AsyncMock(return_value=None)
    enq = mocker.patch("app.modules.receipts.effects.enqueue_merchant_proof_request")

    with pytest.raises(ValidationException):
        await service.request_proof_from_trader_group("uuid-1", 111, "pdf")
    enq.assert_not_called()


# ────────────────────────────────────────────────────────────────
# premoderation / request-proof status gate (security regression)
# ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "settled",
    [OrderStatus.SUCCESS, OrderStatus.FAILED, OrderStatus.CANCELED],
)
async def test_premoderation_dispute_blocked_on_settled_order(
    service, mock_order, stub_change_status, settled
):
    """A premoderation/request-proof dispute must NOT open on a settled/terminal
    order. change_status(SUCCESS -> DISPUTED) reverses the completed settlement
    (reconcile_for_dispute); the trader-reachable request-proof path could
    weaponise that to claw back a paid order, then self-reject to reclaim the
    collateral. The gate returns None (the trader entrypoint surfaces it as a
    ValidationException); no dispute is created and no DISPUTED reversal runs."""
    from app.common.enums.receipt_moderations import ModerationDecision

    mock_order.status = settled
    service.repository.get_by_order_id.return_value = None

    result = await service.open_dispute_from_premoderation(
        mock_order,
        ModerationDecision.REQUEST_PDF,
        initiator_type=UserRole.TRADER,
        initiator_id=mock_order.trader_id,
    )

    assert result is None
    service.repository.create.assert_not_awaited()
    stub_change_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_premoderation_dispute_opens_on_active_order(
    service, mock_order, stub_change_status
):
    """The gate still allows the legitimate active-order path (RECEIPT_UPLOADED)."""
    from app.common.enums.receipt_moderations import ModerationDecision

    mock_order.status = OrderStatus.RECEIPT_UPLOADED
    service.repository.get_by_order_id.return_value = None
    created = MagicMock(spec=Dispute)
    created.id = 5
    service.repository.create.return_value = created

    result = await service.open_dispute_from_premoderation(
        mock_order,
        ModerationDecision.REQUEST_PDF,
        initiator_type=UserRole.TRADER,
        initiator_id=mock_order.trader_id,
    )

    assert result is created
    service.repository.create.assert_awaited_once()
    stub_change_status.assert_awaited_once()
    assert stub_change_status.await_args.args[1] == OrderStatus.DISPUTED
