"""Unit tests for the merchant-dispute-bot intake endpoint (dispute_receipt).

Covers the routing logic: chat→merchant resolution, robust order resolution
(try order-uuid, then external_id — so a UUID-shaped external_id still resolves),
the compact response shape, and the not-found guards. confirm_order itself
(receipt attach + premoderation) is covered by its own tests; here it's mocked.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.bot.v1.endpoints.dispute import _is_uuid, dispute_receipt
from app.core.exceptions import NotFoundException

_UUID = "7996e46f-9a37-43ad-b0e0-d76a182da8f2"
_EXT_UUID = "267883a8-4cb8-4a97-8fe4-c2b0523b4607"  # a merchant external_id that is itself UUID-shaped


def _service(merchant) -> MagicMock:
    """OrderService stub: session.execute(...).scalars().all() → [merchant] (the
    chat→merchant binding may now have several merchants); repository lookups +
    confirm_order are stubbed (default: not found)."""
    svc = MagicMock()
    result = MagicMock()
    merchants = [merchant] if merchant is not None else []
    result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=merchants)))
    svc.session = MagicMock()
    svc.session.execute = AsyncMock(return_value=result)
    svc.repository = MagicMock()
    svc.repository.get_by_uuid_and_merchant = AsyncMock(return_value=None)
    svc.repository.get_by_external_id_and_merchant = AsyncMock(return_value=None)
    return svc


def _order(status_value="receipt_uploaded", moderation="pending") -> MagicMock:
    order = MagicMock()
    order.uuid = _UUID
    order.status = MagicMock(value=status_value)
    order.moderation_status = MagicMock(value=moderation) if moderation else None
    return order


def test_is_uuid():
    assert _is_uuid(_UUID) is True
    assert _is_uuid("my-order-123") is False
    assert _is_uuid("") is False


@pytest.mark.asyncio
async def test_no_merchant_bound_to_chat_raises_404():
    svc = _service(None)  # no merchant for this chat_id
    with pytest.raises(NotFoundException):
        await dispute_receipt(
            chat_id=-100500, identifier=_UUID, attachment=MagicMock(), order_service=svc
        )
    svc.confirm_order.assert_not_called()


@pytest.mark.asyncio
async def test_empty_identifier_raises_404():
    svc = _service(MagicMock())
    with pytest.raises(NotFoundException):
        await dispute_receipt(
            chat_id=-100500, identifier="   ", attachment=MagicMock(), order_service=svc
        )


@pytest.mark.asyncio
async def test_order_not_found_by_either_key_raises_404():
    svc = _service(MagicMock())  # both repo lookups return None (defaults)
    with pytest.raises(NotFoundException):
        await dispute_receipt(
            chat_id=-100500, identifier=_UUID, attachment=MagicMock(), order_service=svc
        )
    svc.confirm_order.assert_not_called()


@pytest.mark.asyncio
async def test_resolves_by_order_uuid():
    merchant = MagicMock()
    svc = _service(merchant)
    order = _order()
    svc.repository.get_by_uuid_and_merchant = AsyncMock(return_value=order)
    svc.confirm_order = AsyncMock(return_value=order)

    resp = await dispute_receipt(
        chat_id=-100500, identifier=_UUID, attachment=MagicMock(), order_service=svc
    )

    svc.repository.get_by_uuid_and_merchant.assert_awaited_once_with(_UUID, merchant.id)
    # Found by uuid → no external fallback needed.
    svc.repository.get_by_external_id_and_merchant.assert_not_called()
    assert svc.confirm_order.await_args.kwargs["order_id"] == _UUID
    assert svc.confirm_order.await_args.kwargs["uploaded_by"] == "merchant_dispute_bot"
    assert resp.order_uuid == _UUID
    assert resp.status == "receipt_uploaded"
    assert resp.moderation_status == "pending"


@pytest.mark.asyncio
async def test_two_merchants_one_chat_resolves_owner_by_order():
    """Two merchants may share one dispute chat — the globally-unique order id
    picks the owner. The endpoint lists ALL bound merchants (never scalar_one) so
    it can't 500, and confirm_order runs against the merchant that owns the order."""
    m1, m2 = MagicMock(), MagicMock()
    m1.id, m2.id = 1, 2
    svc = _service(m1)  # base stub; widen the merchant list to two below
    result = MagicMock()
    result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[m1, m2])))
    svc.session.execute = AsyncMock(return_value=result)
    order = _order()
    # The order belongs to m2 only — m1's scoped lookup misses.
    svc.repository.get_by_uuid_and_merchant = AsyncMock(
        side_effect=lambda cand, mid: order if mid == m2.id else None
    )
    svc.confirm_order = AsyncMock(return_value=order)

    resp = await dispute_receipt(
        chat_id=-100500, identifier=_UUID, attachment=MagicMock(), order_service=svc
    )

    # Resolved against the owning merchant (m2), not m1 — and no 500.
    assert svc.confirm_order.await_args.kwargs["merchant"] is m2
    assert resp.order_uuid == _UUID


@pytest.mark.asyncio
async def test_resolves_by_external_id_when_not_uuid():
    merchant = MagicMock()
    svc = _service(merchant)
    order = _order(moderation=None)
    svc.repository.get_by_external_id_and_merchant = AsyncMock(return_value=order)
    svc.confirm_order = AsyncMock(return_value=order)

    resp = await dispute_receipt(
        chat_id=-100500, identifier="my-order-123", attachment=MagicMock(), order_service=svc
    )

    # Not a uuid → uuid lookup skipped entirely.
    svc.repository.get_by_uuid_and_merchant.assert_not_called()
    svc.repository.get_by_external_id_and_merchant.assert_awaited_once_with(
        "my-order-123", merchant.id
    )
    # confirm_order is always called by the canonical order uuid.
    assert svc.confirm_order.await_args.kwargs["order_id"] == _UUID
    assert resp.moderation_status is None


@pytest.mark.asyncio
async def test_uuid_shaped_external_id_falls_back_to_external_lookup():
    """The key robustness case: the merchant's external_id is itself UUID-shaped.
    The uuid lookup misses (it's not our order uuid) → we fall back to external_id
    and still resolve the order."""
    merchant = MagicMock()
    svc = _service(merchant)
    order = _order()
    svc.repository.get_by_uuid_and_merchant = AsyncMock(return_value=None)  # not our order uuid
    svc.repository.get_by_external_id_and_merchant = AsyncMock(return_value=order)
    svc.confirm_order = AsyncMock(return_value=order)

    resp = await dispute_receipt(
        chat_id=-100500, identifier=_EXT_UUID, attachment=MagicMock(), order_service=svc
    )

    svc.repository.get_by_uuid_and_merchant.assert_awaited_once_with(_EXT_UUID, merchant.id)
    svc.repository.get_by_external_id_and_merchant.assert_awaited_once_with(_EXT_UUID, merchant.id)
    assert svc.confirm_order.await_args.kwargs["order_id"] == _UUID  # canonical uuid of the resolved order
    assert resp.order_uuid == _UUID


@pytest.mark.asyncio
async def test_mask_extracts_our_uuid_from_full_text():
    """New path: the merchant posts a free-form message where THEIR uuid is
    first and OURS is the 2nd uuid. The merchant's mask 'uuid:2' picks ours;
    the bot forwards the full text. THEIR uuid isn't in our system, so even
    without the mask it would be skipped — the mask just makes ours tried first.
    """
    merchant = MagicMock()
    merchant.dispute_id_mask = "uuid:2"
    svc = _service(merchant)
    order = _order()

    async def _by_uuid(u, _mid):
        return order if u == _UUID else None

    svc.repository.get_by_uuid_and_merchant = AsyncMock(side_effect=_by_uuid)
    svc.confirm_order = AsyncMock(return_value=order)

    resp = await dispute_receipt(
        chat_id=-100500,
        attachment=MagicMock(),
        text=f"Апелляция {_EXT_UUID} по заявке номер {_UUID} спасибо",
        order_service=svc,
    )

    # The masked token (2nd uuid = OUR uuid) is resolved first.
    assert svc.repository.get_by_uuid_and_merchant.await_args_list[0].args == (_UUID, merchant.id)
    assert svc.confirm_order.await_args.kwargs["order_id"] == _UUID
    assert resp.order_uuid == _UUID
