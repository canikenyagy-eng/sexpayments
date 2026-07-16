"""TREXO ScamChecker adapter.

API contract: see project's `api.trexo.company` doc — POST /v1/checks
multipart/form-data with a single `file` field. Auth via
`Authorization: Bearer sk_live_<token>`.

Refund rules (per provider docs):
  * `quota_exhausted` (402)         — NOT charged on their side. refundable=True.
  * `upstream_error` (502)          — NOT charged. refundable=True.
  * `upstream_timeout` (504)        — NOT charged. refundable=True.
  * `5xx internal_error`            — NOT charged. refundable=True.
  * `invalid_file` (400)            — NOT charged but our side should not
                                       have allowed it through validation;
                                       treated as refundable=True for safety.
  * `unauthorized` (401)            — refundable=True (provider didn't process).
  * `forbidden` (403)               — refundable=True.
  * 200 with `is_clean` false/true  — billed; refundable=False.

Network exceptions (httpx errors, JSON decode failure) are treated as
refundable=True since we have no proof the provider succeeded.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import httpx

from app.core.security import decrypt_api_secret
from app.modules.receipt_checks.providers.base import ReceiptCheckProviderClient
from app.modules.receipt_checks.schemas import (
    ProviderCheckResult,
    ReceiptCheckVerdictItem,
)

logger = logging.getLogger(__name__)

_REFUNDABLE_ERROR_CODES = {
    "invalid_file",
    "unauthorized",
    "forbidden",
    "quota_exhausted",
    "upstream_error",
    "upstream_timeout",
    "internal_error",
    "method_not_allowed",
    "unsupported_media_type",
    # `file_too_large` and `invalid_request` we surface as refundable too —
    # neither side actually performed work.
    "file_too_large",
    "invalid_request",
    "not_found",
}


class TrexoClient(ReceiptCheckProviderClient):
    def _api_key(self) -> str:
        encrypted = self.provider.api_key_encrypted
        if not encrypted:
            raise RuntimeError("TREXO provider has no API key configured")
        return decrypt_api_secret(encrypted)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._api_key()}"}

    def _timeout(self) -> float:
        return max(float(self.provider.request_timeout_ms) / 1000.0, 5.0)

    async def check_file(self, file_path: str) -> ProviderCheckResult:
        if not os.path.isfile(file_path):
            return ProviderCheckResult(
                refundable=True,
                error_code="invalid_file",
                error_message="Receipt file not present on disk",
            )

        url = f"{self.provider.base_url}/v1/checks"
        filename = os.path.basename(file_path)

        try:
            with open(file_path, "rb") as fh:
                files = {"file": (filename, fh, "application/pdf")}
                async with httpx.AsyncClient(timeout=self._timeout()) as client:
                    resp = await client.post(url, headers=self._headers(), files=files)
        except httpx.TimeoutException:
            logger.warning("TREXO timeout for file %s", file_path)
            return ProviderCheckResult(
                refundable=True,
                error_code="upstream_timeout",
                error_message="TREXO request timed out",
            )
        except httpx.RequestError as exc:
            logger.warning("TREXO request error for file %s: %s", file_path, exc)
            return ProviderCheckResult(
                refundable=True,
                error_code="upstream_error",
                error_message=str(exc),
            )

        try:
            payload = resp.json()
        except Exception:
            return ProviderCheckResult(
                refundable=True,
                error_code="upstream_error",
                error_message=f"Non-JSON response: {resp.text[:200]}",
                raw_response={"status_code": resp.status_code, "text": resp.text[:1000]},
            )

        if resp.status_code == 200:
            verdict_raw = payload.get("verdict") or []
            verdict_items = [
                ReceiptCheckVerdictItem(type=item.get("type", "UNKNOWN"))
                for item in verdict_raw
                if isinstance(item, dict)
            ]
            return ProviderCheckResult(
                is_clean=bool(payload.get("is_clean")),
                verdict=verdict_items,
                parsed_data=payload.get("data") or {},
                provider_check_id=str(payload["check_id"]) if payload.get("check_id") is not None else None,
                provider_tx_id=payload.get("transaction_id"),
                raw_response=payload,
                refundable=False,
            )

        error_code = payload.get("error") or "upstream_error"
        message = payload.get("message") or f"HTTP {resp.status_code}"
        refundable = error_code in _REFUNDABLE_ERROR_CODES or resp.status_code >= 500
        return ProviderCheckResult(
            refundable=refundable,
            error_code=error_code,
            error_message=message,
            raw_response=payload,
        )

    async def get_balance(self) -> dict:
        url = f"{self.provider.base_url}/v1/balance"
        try:
            async with httpx.AsyncClient(timeout=self._timeout()) as client:
                resp = await client.get(url, headers=self._headers())
            payload = resp.json()
        except Exception as exc:
            logger.warning("TREXO balance fetch failed: %s", exc)
            return {"error": f"Failed to fetch balance: {exc}"}

        if resp.status_code != 200:
            return {
                "error": payload.get("message") if isinstance(payload, dict) else f"HTTP {resp.status_code}",
            }
        return {
            "remaining": payload.get("remaining"),
            "own": payload.get("own"),
            "gifted": payload.get("gifted"),
            "total_checks": payload.get("total_checks"),
        }

    async def get_check(self, provider_check_id: str) -> Optional[dict]:
        url = f"{self.provider.base_url}/v1/checks/{provider_check_id}"
        try:
            async with httpx.AsyncClient(timeout=self._timeout()) as client:
                resp = await client.get(url, headers=self._headers())
            if resp.status_code == 200:
                return resp.json()
        except Exception as exc:
            logger.warning("TREXO get_check failed for %s: %s", provider_check_id, exc)
        return None
