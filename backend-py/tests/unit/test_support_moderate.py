"""Unit tests for the moderation-decision path.

The shared ``receipts.moderation.coordinator.apply_moderation_decision`` is the
SINGLE site behind BOTH entry points — the support-bot inline keyboard and the
admin web UI — so they can never drift (and never drift from confirm_order's
auto-approve, which fires the same ``receipts.effects`` bundle).

Covers:
  * coordinator: ACCEPT fires the approved bundle; REJECT opens the dispute +
    requests merchant proof.
  * admin decide endpoint: delegates to the coordinator with the acting admin's
    login as moderator; 404 when the order is missing.
"""
import uuid as uuidlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.common.enums.receipt_moderations import ModerationDecision
from app.modules.receipts.moderation.coordinator import apply_moderation_decision


# ─── shared coordinator ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_accept_fires_shared_approved_bundle():
    order = MagicMock()
    order.id = 100
    with (
        patch("app.modules.receipts.moderation.coordinator.ReceiptModerationService") as Svc,
        patch("app.modules.receipts.moderation.coordinator.receipt_effects") as eff,
    ):
        Svc.return_value.apply_decision = AsyncMock(
            return_value=MagicMock(decision=ModerationDecision.ACCEPT)
        )
        row = await apply_moderation_decision(
            session=MagicMock(), order=order,
            decision=ModerationDecision.ACCEPT, moderator_username="a",
        )
    Svc.return_value.apply_decision.assert_awaited_once()
    eff.fire_receipt_approved.assert_called_once_with(100, notify_trader=True, notify_countdown=1)
    eff.enqueue_merchant_proof_request.assert_not_called()
    assert row.decision == ModerationDecision.ACCEPT


@pytest.mark.asyncio
async def test_reject_opens_dispute_and_requests_proof():
    order = MagicMock()
    order.id = 100
    with (
        patch("app.modules.receipts.moderation.coordinator.ReceiptModerationService") as Svc,
        patch("app.modules.receipts.moderation.coordinator.receipt_effects") as eff,
        patch("app.modules.disputes.service.DisputeService") as Disp,
    ):
        Svc.return_value.apply_decision = AsyncMock(
            return_value=MagicMock(decision=ModerationDecision.REQUEST_PDF)
        )
        Disp.return_value.open_dispute_from_premoderation = AsyncMock()
        await apply_moderation_decision(
            session=MagicMock(), order=order,
            decision=ModerationDecision.REQUEST_PDF, moderator_username="a",
        )
    Disp.return_value.open_dispute_from_premoderation.assert_awaited_once()
    eff.enqueue_merchant_proof_request.assert_called_once_with(100, "request_pdf")
    eff.fire_receipt_approved.assert_not_called()


# ─── admin web-UI decide endpoint ──────────────────────────────────────

@pytest.mark.asyncio
async def test_admin_decide_delegates_to_coordinator_with_admin_login():
    from app.api.v1.endpoints import receipt_moderations as rm

    order = MagicMock()
    order.id = 100
    order.uuid = uuidlib.uuid4()
    row = MagicMock()
    item = MagicMock()
    mod_svc = MagicMock()
    mod_svc.session = MagicMock()
    admin = MagicMock(username="admin_bob")
    payload = MagicMock(decision=ModerationDecision.ACCEPT)

    with (
        patch.object(rm, "OrderRepository") as Repo,
        patch.object(rm, "apply_moderation_decision", AsyncMock(return_value=row)) as coord,
        patch.object(rm, "_fetch_trader_usernames", AsyncMock(return_value={})),
        patch.object(rm, "_item_from_row", return_value=item),
    ):
        Repo.return_value.get = AsyncMock(return_value=order)
        result = await rm.decide_moderation(
            order_id=100, payload=payload, current_user=admin, moderation_service=mod_svc,
        )

    coord.assert_awaited_once()
    _, kwargs = coord.await_args
    assert kwargs["order"] is order
    assert kwargs["decision"] == ModerationDecision.ACCEPT
    # The acting admin's login is recorded as the moderator (no Telegram id).
    assert kwargs["moderator_username"] == "admin_bob"
    assert result is item


@pytest.mark.asyncio
async def test_admin_decide_404_when_order_missing():
    from app.api.v1.endpoints import receipt_moderations as rm
    from app.core.exceptions import NotFoundException

    mod_svc = MagicMock()
    mod_svc.session = MagicMock()
    with patch.object(rm, "OrderRepository") as Repo:
        Repo.return_value.get = AsyncMock(return_value=None)
        with pytest.raises(NotFoundException):
            await rm.decide_moderation(
                order_id=999,
                payload=MagicMock(decision=ModerationDecision.ACCEPT),
                current_user=MagicMock(username="a"),
                moderation_service=mod_svc,
            )
