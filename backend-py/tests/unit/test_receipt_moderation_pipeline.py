"""Unit tests for the receipt-moderation policy + decision engine
(``ReceiptModerationService.evaluate``).

Locks the behaviour-preserving default (no enabled auto-checker → review-required
escalates to a human, else auto-approve) AND proves the plug-in point: an
enabled checker with a confident verdict short-circuits human review.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.receipts.moderation import (
    CheckDecision,
    CheckVerdict,
    ModerationOutcome,
    ModerationResolution,
    ReceiptModerationPolicy,
    ReceiptModerationService,
)


def _svc():
    return ReceiptModerationService(session=MagicMock())


def _patch_resolution(res):
    return patch.object(
        ReceiptModerationPolicy, "resolve", AsyncMock(return_value=res)
    )


class _Checker:
    def __init__(self, decision, confidence=1.0, enabled=True, name="t"):
        self._v = CheckVerdict(decision, checker=name, confidence=confidence)
        self.enabled = enabled
        self.name = name

    async def check(self, *, order, merchant, session):
        return self._v


# ── policy ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_policy_flag_off_no_review():
    pol = ReceiptModerationPolicy(session=MagicMock())
    pol.is_enabled_for = AsyncMock(return_value=False)
    res = await pol.resolve(order=MagicMock(), merchant=MagicMock())
    assert res.review_required is False


@pytest.mark.asyncio
async def test_policy_flag_on_with_chat_requires_review():
    pol = ReceiptModerationPolicy(session=MagicMock())
    pol.is_enabled_for = AsyncMock(return_value=True)
    with patch("app.modules.settings.service.SettingsService") as S:
        S.return_value.get_str = AsyncMock(return_value="-100999")
        res = await pol.resolve(order=MagicMock(), merchant=MagicMock())
    assert res.review_required is True and res.support_chat_id == -100999


@pytest.mark.asyncio
async def test_policy_flag_on_no_chat_falls_back_to_off():
    pol = ReceiptModerationPolicy(session=MagicMock())
    pol.is_enabled_for = AsyncMock(return_value=True)
    with patch("app.modules.settings.service.SettingsService") as S:
        S.return_value.get_str = AsyncMock(return_value="")
        res = await pol.resolve(order=MagicMock(), merchant=MagicMock())
    assert res.review_required is False and res.misconfigured is True


# ── decision engine (behaviour-preserving default) ──────────────────


@pytest.mark.asyncio
async def test_evaluate_no_review_required_auto_approves():
    with _patch_resolution(ModerationResolution(review_required=False)):
        out = await _svc().evaluate(order=MagicMock(), merchant=MagicMock())
    assert out.outcome == ModerationOutcome.AUTO_APPROVE


@pytest.mark.asyncio
async def test_evaluate_review_required_no_checkers_escalates_to_human():
    """Today's behaviour: premoderation on + no auto-checker → support-bot."""
    with _patch_resolution(ModerationResolution(review_required=True, support_chat_id=-100777)):
        out = await _svc().evaluate(order=MagicMock(), merchant=MagicMock())
    assert out.outcome == ModerationOutcome.ESCALATE_HUMAN
    assert out.support_chat_id == -100777


# ── decision engine (the plug-in point — an enabled checker) ────────


@pytest.mark.asyncio
async def test_evaluate_confident_auto_approve_skips_human():
    with _patch_resolution(ModerationResolution(review_required=True, support_chat_id=1)), \
         patch("app.modules.receipts.moderation.service._AUTO_CHECKERS",
               [_Checker(CheckDecision.APPROVE, confidence=1.0)]):
        out = await _svc().evaluate(order=MagicMock(), merchant=MagicMock())
    assert out.outcome == ModerationOutcome.AUTO_APPROVE


@pytest.mark.asyncio
async def test_evaluate_confident_reject_auto_rejects():
    with _patch_resolution(ModerationResolution(review_required=True, support_chat_id=1)), \
         patch("app.modules.receipts.moderation.service._AUTO_CHECKERS",
               [_Checker(CheckDecision.REJECT, confidence=1.0, name="fraud")]):
        out = await _svc().evaluate(order=MagicMock(), merchant=MagicMock())
    assert out.outcome == ModerationOutcome.AUTO_REJECT
    assert out.reject_reason == ""  # carried from the verdict


@pytest.mark.asyncio
async def test_evaluate_uncertain_checker_escalates_to_human():
    with _patch_resolution(ModerationResolution(review_required=True, support_chat_id=5)), \
         patch("app.modules.receipts.moderation.service._AUTO_CHECKERS",
               [_Checker(CheckDecision.APPROVE, confidence=0.6)]):  # below threshold
        out = await _svc().evaluate(order=MagicMock(), merchant=MagicMock())
    assert out.outcome == ModerationOutcome.ESCALATE_HUMAN


@pytest.mark.asyncio
async def test_evaluate_disabled_checker_is_skipped():
    with _patch_resolution(ModerationResolution(review_required=True, support_chat_id=5)), \
         patch("app.modules.receipts.moderation.service._AUTO_CHECKERS",
               [_Checker(CheckDecision.APPROVE, confidence=1.0, enabled=False)]):
        out = await _svc().evaluate(order=MagicMock(), merchant=MagicMock())
    # Disabled checker ignored → falls through to human.
    assert out.outcome == ModerationOutcome.ESCALATE_HUMAN
