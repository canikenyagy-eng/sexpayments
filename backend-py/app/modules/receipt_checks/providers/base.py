"""Abstract receipt-check provider client.

Service layer talks to providers only through this interface — adding a
new vendor is a single new module under `providers/` + a registration in
`providers/__init__.py`.

All adapters MUST:
  * never raise for "billable" failures (file rejected, quota exhausted) —
    those are returned as ProviderCheckResult with refundable=False;
  * return refundable=True when the provider explicitly did not charge
    them (5xx, network timeout). The service then reverses the trader's
    USDT debit before returning to the caller.
"""
from __future__ import annotations

import abc
from typing import Optional

from app.modules.receipt_checks.models import ReceiptCheckProvider
from app.modules.receipt_checks.schemas import ProviderCheckResult


class ReceiptCheckProviderClient(abc.ABC):
    def __init__(self, provider: ReceiptCheckProvider):
        self.provider = provider

    @abc.abstractmethod
    async def check_file(self, file_path: str) -> ProviderCheckResult:
        """Run one verification against the file on disk."""

    @abc.abstractmethod
    async def get_balance(self) -> dict:
        """Pull remote quota/balance. Returned as a free-form dict so the
        admin UI renders provider-specific buckets without leaking adapter
        details into the service layer."""

    # Most adapters support /v1/checks/{id} re-fetch; not all do, hence
    # the default returns None.
    async def get_check(self, provider_check_id: str) -> Optional[dict]:  # noqa: D401
        return None
