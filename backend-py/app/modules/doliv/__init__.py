"""Долив — requisite refill.

A долив is a payout a trader requests against their OWN under-used payin
requisite: the trader funds it (WORK→ESCROW freeze of amount + price), a
"доливщик" (a trader from the platform-settings executor list) really sends the
money to the card, and on completion the requesting trader is debited, the
доливщик is reimbursed the amount + a reward, and the requisite's daily+monthly
turnover is filled.

It is stored as a ``Payout`` row flagged ``is_doliv`` (so it surfaces in the
existing payout pool / "my payouts" views), but ``DolivService`` owns its money
flow and lifecycle — the money-critical ``PayoutService.change_status`` is left
untouched.
"""
from app.modules.doliv.service import DolivService  # noqa: F401

__all__ = ["DolivService"]
