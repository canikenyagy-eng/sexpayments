"""detect.io receipt-check adapter.

API contract: detect.io B2B API — host ``https://cheque.wales`` (the adapter
owns the ``/api/v1`` prefix; the configured base_url may be the host or the full
``https://cheque.wales/api/v1`` root — see ``_base_v1``).
Auth via ``Authorization: Bearer <token>``. The receipt is a PDF uploaded as
``multipart/form-data`` (field ``file``); the service layer already rejects
non-PDF files before we get here, so we always send ``application/pdf``.

  * ``POST /verify``                  — run the check (paid). Returns
    ``{request_id, verdict, bank, checked_at}``.
  * ``GET /verify/{request_id}``      — re-fetch a past result (free).
  * ``GET /billing/balance``          — ``{balance, price, currency, enabled}`` (free).

Billing (per docs): a verdict of ``original`` or ``fake`` is charged on their
side; ``unknown_bank`` and any error are free. We mirror that into
``refundable`` so the service reverses the trader's USDT debit whenever
detect.io did NOT charge us:

  verdict ``original``      → is_clean=True,  billed,    refundable=False
  verdict ``fake``         → is_clean=False, billed,    refundable=False
  verdict ``unknown_bank``  → error,         not billed, refundable=True
  verdict ``cannot_process``→ error (wins),  not billed, refundable=True
  402 insufficient_balance  → error,         not billed, refundable=True
  401 / 400 / 404 / 5xx     → error,         not billed, refundable=True

Network exceptions (httpx errors, JSON decode failure) are refundable=True —
we have no proof the provider performed (and billed) the check.
"""
from __future__ import annotations

import logging
import os
from decimal import Decimal, InvalidOperation
from typing import Optional

import httpx

from app.core.security import decrypt_api_secret
from app.modules.receipt_checks.providers.base import ReceiptCheckProviderClient
from app.modules.receipt_checks.schemas import (
    ProviderCheckResult,
    ReceiptCheckVerdictItem,
)

logger = logging.getLogger(__name__)

# Verdicts that detect.io charges for and that yield a clean/fake decision.
_VERDICT_ORIGINAL = "original"
_VERDICT_FAKE = "fake"
# Free, non-decisive verdicts → surfaced as a (refundable) error so the
# service reverses our charge. ``cannot_process`` outranks any other verdict.
_VERDICT_UNKNOWN_BANK = "unknown_bank"
_VERDICT_CANNOT_PROCESS = "cannot_process"


class DetectioClient(ReceiptCheckProviderClient):
    def _api_key(self) -> str:
        encrypted = self.provider.api_key_encrypted
        if not encrypted:
            raise RuntimeError("detect.io provider has no API key configured")
        return decrypt_api_secret(encrypted)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._api_key()}"}

    def _timeout(self) -> float:
        return max(float(self.provider.request_timeout_ms) / 1000.0, 5.0)

    def _base_v1(self) -> str:
        """detect.io's API lives under ``/api/v1``. Own that prefix here (like
        the TREXO adapter owns ``/v1``) so the admin can configure the base_url
        as either the host (``https://cheque.wales``) or the full API root
        (``https://cheque.wales/api/v1``) — both resolve correctly, avoiding the
        404-then-non-JSON balance error from a missing ``/api/v1``."""
        base = (self.provider.base_url or "").rstrip("/")
        if base.endswith("/api/v1"):
            base = base[: -len("/api/v1")]
        return f"{base}/api/v1"

    async def check_file(self, file_path: str) -> ProviderCheckResult:
        if not os.path.isfile(file_path):
            return ProviderCheckResult(
                refundable=True,
                error_code="invalid_file",
                error_message="Receipt file not present on disk",
            )

        url = f"{self._base_v1()}/verify"
        filename = os.path.basename(file_path)

        try:
            with open(file_path, "rb") as fh:
                files = {"file": (filename, fh, "application/pdf")}
                async with httpx.AsyncClient(timeout=self._timeout()) as client:
                    resp = await client.post(url, headers=self._headers(), files=files)
        except httpx.TimeoutException:
            logger.warning("detect.io timeout for file %s", file_path)
            return ProviderCheckResult(
                refundable=True,
                error_code="upstream_timeout",
                error_message="detect.io request timed out",
            )
        except httpx.RequestError as exc:
            logger.warning("detect.io request error for file %s: %s", file_path, exc)
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

        if resp.status_code == 200 and isinstance(payload, dict):
            return self._parse_verdict(payload)

        return self._parse_http_error(resp, payload)

    # ─── helpers ────────────────────────────────────────────────

    def _parse_verdict(self, payload: dict) -> ProviderCheckResult:
        verdict = str(payload.get("verdict") or "").lower()
        bank = payload.get("bank")
        request_id = payload.get("request_id")
        parsed = {
            "verdict": verdict or None,
            "bank": bank,
            "checked_at": payload.get("checked_at"),
        }
        check_id = str(request_id) if request_id is not None else None

        # ``cannot_process`` outranks every other verdict (docs).
        if verdict == _VERDICT_CANNOT_PROCESS:
            return ProviderCheckResult(
                refundable=True,
                error_code="cannot_process",
                error_message="detect.io could not process the receipt",
                provider_check_id=check_id,
                raw_response=payload,
            )
        if verdict == _VERDICT_ORIGINAL:
            return ProviderCheckResult(
                is_clean=True,
                verdict=[],  # no red flags — bank lives in parsed_data
                parsed_data=parsed,
                provider_check_id=check_id,
                raw_response=payload,
                refundable=False,
            )
        if verdict == _VERDICT_FAKE:
            return ProviderCheckResult(
                is_clean=False,
                verdict=[ReceiptCheckVerdictItem(type="fake", message=f"Банк: {bank}" if bank else "Подделка")],
                parsed_data=parsed,
                provider_check_id=check_id,
                raw_response=payload,
                refundable=False,
            )
        if verdict == _VERDICT_UNKNOWN_BANK:
            return ProviderCheckResult(
                refundable=True,
                error_code="unknown_bank",
                error_message="Банк не распознан",
                provider_check_id=check_id,
                raw_response=payload,
            )
        # Unexpected/empty verdict on a 200 — treat as a non-billable anomaly.
        return ProviderCheckResult(
            refundable=True,
            error_code="unexpected_verdict",
            error_message=f"Unexpected verdict: {verdict!r}",
            provider_check_id=check_id,
            raw_response=payload,
        )

    def _parse_http_error(self, resp: httpx.Response, payload) -> ProviderCheckResult:
        status = resp.status_code
        body = payload if isinstance(payload, dict) else {}

        if status == 402:
            # insufficient_balance — body carries balance/price/currency.
            detail = body.get("error") or "insufficient_balance"
            bal, price, cur = body.get("balance"), body.get("price"), body.get("currency")
            return ProviderCheckResult(
                refundable=True,
                error_code="insufficient_balance",
                error_message=f"{detail}: balance={bal} price={price} {cur}",
                raw_response=body or None,
            )

        code_by_status = {
            400: "invalid_request",
            401: "unauthorized",
            404: "not_found",
        }
        error_code = body.get("error") or code_by_status.get(status)
        if not error_code:
            error_code = "internal_error" if status >= 500 else "upstream_error"

        message = body.get("message") or body.get("error") or f"HTTP {status}"
        # detect.io only ever bills a 200 + ``original``/``fake`` verdict; every
        # non-200 means no verdict was produced and nothing was charged → always
        # refund the trader's debit (covers undocumented statuses like 403/429).
        return ProviderCheckResult(
            refundable=True,
            error_code=error_code,
            error_message=message,
            raw_response=body or None,
        )

    async def get_balance(self) -> dict:
        url = f"{self._base_v1()}/billing/balance"
        try:
            async with httpx.AsyncClient(timeout=self._timeout()) as client:
                resp = await client.get(url, headers=self._headers())
        except Exception as exc:
            logger.warning("detect.io balance fetch failed: %s", exc)
            return {"error": f"Не удалось связаться с detect.io: {exc}"}

        try:
            payload = resp.json()
        except Exception:
            # Non-JSON body (usually an HTML 404) → almost always a wrong Base URL.
            return {
                "error": (
                    f"detect.io вернул не-JSON (HTTP {resp.status_code}). "
                    f"Проверьте Base URL — нужен https://cheque.wales"
                )
            }

        if resp.status_code != 200 or not isinstance(payload, dict):
            msg = payload.get("message") if isinstance(payload, dict) else None
            return {"error": msg or f"HTTP {resp.status_code}"}

        if payload.get("enabled") is False:
            return {"error": "Аккаунт detect.io отключён"}

        # detect.io reports a USDT money balance + per-check price. The admin UI
        # field ``remaining`` means "checks left" — derive it as how many checks
        # the balance can still afford (floor(balance / price)).
        remaining = self._affordable_checks(payload.get("balance"), payload.get("price"))
        return {"remaining": remaining}

    @staticmethod
    def _affordable_checks(balance, price) -> Optional[int]:
        try:
            bal = Decimal(str(balance))
            prc = Decimal(str(price))
        except (InvalidOperation, TypeError, ValueError):
            return None
        if prc <= 0:
            return None
        return int(bal // prc)

    async def get_check(self, provider_check_id: str) -> Optional[dict]:
        url = f"{self._base_v1()}/verify/{provider_check_id}"
        try:
            async with httpx.AsyncClient(timeout=self._timeout()) as client:
                resp = await client.get(url, headers=self._headers())
            if resp.status_code == 200:
                return resp.json()
        except Exception as exc:
            logger.warning("detect.io get_check failed for %s: %s", provider_check_id, exc)
        return None
