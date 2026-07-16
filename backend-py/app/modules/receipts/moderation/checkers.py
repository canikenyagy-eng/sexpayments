"""Automatic receipt checkers — the pluggable verification steps the
moderation decision engine runs before falling back to a human.

This is the seam where a future internal model plugs in: implement
``ReceiptChecker`` (e.g. OCR amount-match, an ML/LLM verifier), register it via
``register_checker`` and flip ``enabled`` — ``ReceiptModerationService.evaluate``
never changes. Today **no auto-checker is enabled**, so evaluate reproduces the
historical behaviour exactly.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import List, Protocol, runtime_checkable


class CheckDecision(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    ESCALATE = "escalate"   # not sure — defer to the next checker / human


@dataclass
class CheckVerdict:
    decision: CheckDecision
    checker: str
    confidence: float = 1.0
    reason: str = ""


@runtime_checkable
class ReceiptChecker(Protocol):
    """One automatic verification step. Synchronous verdict (no human wait)."""
    name: str
    enabled: bool

    async def check(self, *, order, merchant, session) -> CheckVerdict: ...


class ModelReceiptChecker:
    """Placeholder for a future internal model (OCR / ML / LLM) that reads the
    receipt and judges whether it's a valid proof for the order's amount and
    requisite. Disabled until a model is wired — registered here only to pin the
    integration point. When enabled and confident it can short-circuit human
    review."""
    name = "model"
    enabled = False

    async def check(self, *, order, merchant, session) -> CheckVerdict:  # pragma: no cover — stub
        return CheckVerdict(CheckDecision.ESCALATE, checker=self.name, reason="model not wired")


# Ordered registry of automatic checkers. Empty of *enabled* entries today →
# behaviour-preserving. Append + flip ``enabled`` to roll out a new checker.
_AUTO_CHECKERS: List[ReceiptChecker] = [ModelReceiptChecker()]


def register_checker(checker: ReceiptChecker) -> None:
    _AUTO_CHECKERS.append(checker)
