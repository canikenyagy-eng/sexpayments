"""Bitzone cascade adapter.

Docs: https://developers.bitzone.space/

Auth scheme is the simplest of all our adapters:
  * outbound — a single ``x-api-key`` header carrying the merchant API key as
    raw text. No bearer prefix, no timestamp, no body signature.
  * inbound  — webhook signed with ``hex(HMAC_SHA256(api_key, raw_body))`` in
    the ``x-signature`` header. Same API key for both directions, so we set
    ``WEBHOOK_SECRET_SOURCE = "api_secret"`` (the api_secret_encrypted column
    is where we keep the key locally).

The dispute flow is two-step: upload the proof file via
``/file/trading/pay-in/invoice/upload?tradeId=<id>`` to receive an
``invoiceKey``, then POST that key plus the fiat amount to
``/payment/trading/pay-in/<id>/dispute``. ``raise_dispute`` walks both steps;
``notify_receipt`` only does the upload (the receipt becomes the invoiceKey
the customer can use to confirm payment on Bitzone's hosted page).

Status mapping (Bitzone → ProviderStatus):
  pending         → CREATED  (queued for processing)
  active          → PENDING  (awaiting payment from the customer)
  paid            → PAID     (paid; awaiting their internal close)
  checking        → DISPUTED (under review during a dispute)
  dispute         → DISPUTED
  re_calculation  → DISPUTED (post-dispute amount adjustment)
  closed          → SUCCESS  (final)
  canceled        → CANCELED (final)

Per-provider settings (``CascadeProvider.settings``):
  callback_url            — URL passed in every pay-in body so Bitzone knows
                            where to deliver the webhook
  default_currency        — fiat currency (RUB by default)
  method_map              — our PaymentMethod → Bitzone ``method`` value
                            (sbp/card/qr/cross_sbp/mobile_comm/etc).
                            Empty = sbp→sbp, card→card, sim→mobile_comm.
  bank_code_map           — our PaymentOption.code → Bitzone ``bank`` value
                            (sber → SBER). Empty = uppercase passthrough.
  deposit_type            — FTD/STD; sent on every pay-in for fraud signals.
  expires_default_seconds — TTL used when the response carries no payment
                            window (default 1800).
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Optional

import httpx

from app.common.enums.cascading import ProviderStatus
from app.common.enums.payments import PaymentMethod
from app.core.logging import get_logger
from app.modules.cascading.integrations.base import (
    AdapterFieldOption,
    AdapterFieldSpec,
    CallbackVerificationError,
    IssueResult,
    ParsedCallback,
    PayinRequest,
    ProviderAdapter,
    ProviderRefusal,
    ProviderRequisiteResponse,
)
from app.modules.cascading.models import CascadeProvider

logger = get_logger(__name__)


class BitzoneAdapter(ProviderAdapter):
    code = "bitzone"
    display_name = "Bitzone"
    description = (
        "P2P-площадка api.bitzone.space. Поддерживает Card, SBP (sbp/qr), "
        "SIM (mobile_comm) и cross-border варианты. Авторизация — один "
        "x-api-key. Курс берётся из их ответа (currencyRate)."
    )
    supports_provider_rate = True

    SUPPORTED_METHODS = (
        PaymentMethod.SBP,
        PaymentMethod.CARD,
        PaymentMethod.SIM,
    )

    # ─── Signing knobs ───
    # One header, raw value, no timestamp, no body signing.
    AUTH_HEADER = "x-api-key"
    AUTH_SCHEME = ""
    SIGN_REQUESTS = False
    IDEMPOTENCY_HEADER = ""  # provider has no idempotency channel; use externalTransactionId
    # Inbound webhook signs with the same API key we use for outbound auth.
    WEBHOOK_SECRET_SOURCE = "api_secret"
    SIGNATURE_HEADER = "x-signature"

    # ─── Аутентификация: один ключ на всё ───
    CREDENTIALS_SCHEMA = (
        AdapterFieldSpec(
            key="api_secret",
            label="x-api-key (он же ключ для webhook)",
            type="string",
            secret=True,
            required=True,
            description=(
                "API-ключ из кабинета Bitzone. Шлётся в заголовке "
                "x-api-key. Этот же ключ Bitzone использует как HMAC "
                "secret для подписи webhook'ов — отдельное поле "
                "Webhook secret здесь не нужно."
            ),
        ),
    )

    # ─── Declarative method/bank mappings ───
    DEFAULT_METHOD_MAP = {
        PaymentMethod.SBP.value: "sbp",
        PaymentMethod.CARD.value: "card",
        PaymentMethod.SIM.value: "mobile_comm",
    }
    PROVIDER_METHOD_TO_OURS = {
        "sbp": PaymentMethod.SBP,
        "qr": PaymentMethod.SBP,
        "cross_sbp": PaymentMethod.SBP,
        "abkhaz_sbp": PaymentMethod.SBP,
        "abkhaz_qr": PaymentMethod.SBP,
        "card": PaymentMethod.CARD,
        "cross_card": PaymentMethod.CARD,
        "abkhaz_card": PaymentMethod.CARD,
        "account": PaymentMethod.CARD,
        "link": PaymentMethod.CARD,
        "upi_intent": PaymentMethod.CARD,
        "mobile_comm": PaymentMethod.SIM,
    }
    BANK_CODE_TRANSFORM = "upper"
    REQUIRED_CREDENTIALS = ("api_secret",)

    PROVIDER_STATUS_MAP = {
        "pending": ProviderStatus.CREATED,
        "active": ProviderStatus.PENDING,
        "paid": ProviderStatus.PAID,
        "checking": ProviderStatus.DISPUTED,
        "dispute": ProviderStatus.DISPUTED,
        "re_calculation": ProviderStatus.DISPUTED,
        "closed": ProviderStatus.SUCCESS,
        "canceled": ProviderStatus.CANCELED,
    }

    SETTINGS_SCHEMA = (
        AdapterFieldSpec(
            key="callback_url",
            label="Webhook URL для каждого pay-in запроса",
            type="url",
            description=(
                "Передаётся в callbackUrl при создании заявки и в Settings "
                "личного кабинета Bitzone. Должен соответствовать "
                "https://<host>/api/cascade/v1/callbacks/<provider_code>."
            ),
            placeholder="https://example.com/api/cascade/v1/callbacks/bitzone",
        ),
        AdapterFieldSpec(
            key="default_currency",
            label="Фиатная валюта по умолчанию",
            type="string",
            default="RUB",
            placeholder="RUB",
        ),
        AdapterFieldSpec(
            key="deposit_type",
            label="depositType",
            type="select",
            default="STD",
            options=[
                AdapterFieldOption("FTD", "FTD (первый депозит клиента)"),
                AdapterFieldOption("STD", "STD (повторный депозит)"),
            ],
            description=(
                "Отправляется в каждый pay-in. Помогает их fraud-системе "
                "оценивать риск; не влияет на доступность реквизитов."
            ),
        ),
        AdapterFieldSpec(
            key="expires_default_seconds",
            label="TTL реквизита (сек)",
            type="number",
            default=1800,
            min=60,
            max=86400,
            description="Используется, если Bitzone не передал явный таймаут.",
        ),
        AdapterFieldSpec(
            key="method_map",
            label="Маппинг методов (наш → Bitzone method)",
            type="kv_map",
            value_type="select",
            options=[
                AdapterFieldOption("sbp", "sbp"),
                AdapterFieldOption("qr", "qr"),
                AdapterFieldOption("card", "card"),
                AdapterFieldOption("account", "account"),
                AdapterFieldOption("cross_sbp", "cross_sbp"),
                AdapterFieldOption("cross_card", "cross_card"),
                AdapterFieldOption("mobile_comm", "mobile_comm"),
                AdapterFieldOption("link", "link"),
                AdapterFieldOption("upi_intent", "upi_intent"),
            ],
            description=(
                "Closed whitelist при заполнении. Ключи — наши коды "
                "(sbp/card/sim), значения — что отправлять в Bitzone method. "
                "Пусто = sbp→sbp, card→card, sim→mobile_comm."
            ),
        ),
        AdapterFieldSpec(
            key="bank_code_map",
            label="Маппинг банков (наш PaymentOption.code → Bitzone bank)",
            type="kv_map",
            value_type="string",
            description=(
                "Ключ — наш PaymentOption.code (sber, tinkoff), значение — "
                "код банка для Bitzone (SBER, TBANK). Пусто = uppercase нашего кода."
            ),
        ),
    )

    # ─── ProviderAdapter contract ──────────────────────────────
    # supports() inherited from base — uses SUPPORTED_METHODS + resolve_method_value.
    # issue_requisite() inherited — calls build_payin_request → parse_payin_response.

    def build_payin_request(
        self,
        *,
        provider: CascadeProvider,
        order_data: Dict[str, Any],
        idempotency_key: str,
        method: PaymentMethod,
        method_value: str,
    ):
        settings = self.get_settings(provider)
        body: Dict[str, Any] = {
            "fiatAmount": float(Decimal(str(order_data["amount"]))),
            "fiatCurrency": settings.get("default_currency", "RUB"),
            "method": method_value,
            "extra": {
                "externalTransactionId": (
                    order_data.get("merchant_request_id") or idempotency_key
                ),
            },
        }
        bank = self.resolve_bank_code(provider, order_data.get("payment_option_code"))
        if bank:
            body["bank"] = bank
        deposit_type = settings.get("deposit_type")
        if deposit_type:
            body["depositType"] = deposit_type
        callback_url = (
            order_data.get("bitzone_callback_url")
            or settings.get("callback_url")
            or self.build_cascade_callback_url(provider.code)
        )
        if callback_url:
            body["callbackUrl"] = str(callback_url)
        payer_info = order_data.get("payer_info")
        if isinstance(payer_info, dict):
            body["extra"]["payerInfo"] = payer_info
        return PayinRequest(
            http_method="POST",
            path="/payment/trading/pay-in",
            body=body,
        )

    async def cancel_request(
        self,
        *,
        provider: CascadeProvider,
        external_order_id: str,
        timeout_ms: int,
    ) -> bool:
        resp = await self.safe_request(
            provider=provider,
            method="POST",
            path=f"/payment/trading/pay-in/{external_order_id}/cancel",
            body=None,
            timeout_ms=timeout_ms,
            log_event="cancel_network_error",
        )
        return resp is not None and resp.status_code in (200, 404)

    async def notify_receipt(
        self,
        *,
        provider: CascadeProvider,
        external_order_id: str,
        receipt_path: str,
        comment: Optional[str],
    ) -> bool:
        """Forward the receipt to /file/trading/pay-in/invoice/upload.

        Bitzone returns an ``invoiceKey`` from this call which is later used to
        anchor a dispute. We discard it here — ``raise_dispute`` re-uploads
        when it actually needs an invoiceKey.
        """
        if not receipt_path:
            return True
        invoice_key = await self._upload_invoice(
            provider=provider,
            external_order_id=external_order_id,
            file_path=receipt_path,
        )
        return invoice_key is not None

    async def raise_dispute(
        self,
        *,
        provider: CascadeProvider,
        external_order_id: str,
        reason: str,
        evidence_paths: List[str],
    ) -> bool:
        if not evidence_paths:
            logger.warning(
                "bitzone_dispute_no_evidence",
                provider_id=provider.id,
                external_order_id=external_order_id,
            )
            return False

        # Dispute body requires the fiatAmount the user actually paid. We
        # don't always have that locally — pull it from the trade record.
        fiat_amount = await self._fetch_fiat_amount(provider, external_order_id)
        if fiat_amount is None:
            return False

        ok = True
        for path in evidence_paths:
            invoice_key = await self._upload_invoice(
                provider=provider,
                external_order_id=external_order_id,
                file_path=path,
            )
            if not invoice_key:
                ok = False
                continue
            body = {
                "invoiceKey": invoice_key,
                "fiatAmount": fiat_amount,
                "comment": reason or "Dispute raised by merchant",
            }
            resp = await self.safe_request(
                provider=provider,
                method="POST",
                path=f"/payment/trading/pay-in/{external_order_id}/dispute",
                body=body,
                timeout_ms=provider.request_timeout_ms,
                log_event="dispute_submit_failed",
            )
            if resp is None or resp.status_code not in (200, 201):
                ok = False
        return ok

    def parse_callback(
        self,
        *,
        provider: CascadeProvider,
        headers: Dict[str, str],
        body: bytes,
    ) -> ParsedCallback:
        payload = self.verify_and_decode_callback(
            provider=provider, headers=headers, body=body
        )

        external_id = payload.get("id")
        if not external_id:
            raise CallbackVerificationError("Webhook missing id")

        status = self.parse_provider_status(payload.get("status"))

        paid_amount: Optional[Decimal] = None
        if status in (ProviderStatus.SUCCESS, ProviderStatus.PAID):
            paid_amount = self.safe_decimal(payload.get("fiatAmount"))

        return ParsedCallback(
            external_order_id=str(external_id),
            status=status,
            raw=payload,
            paid_amount_fiat=paid_amount,
        )

    async def poll_status(
        self,
        *,
        provider: CascadeProvider,
        external_order_id: str,
        timeout_ms: int,
    ) -> Optional[ParsedCallback]:
        resp = await self.safe_request(
            provider=provider,
            method="GET",
            path=f"/payment/trading/{external_order_id}",
            timeout_ms=timeout_ms,
            log_event="poll_failed",
        )
        if resp is None or resp.status_code != 200:
            return None
        envelope = self.json_or_none(resp) or {}
        status = self.try_parse_provider_status(envelope.get("status"))
        if status is None:
            return None
        return ParsedCallback(
            external_order_id=str(envelope.get("id") or external_order_id),
            status=status,
            raw={"event": "polled", "envelope": envelope},
        )

    async def get_balance(
        self,
        *,
        provider: CascadeProvider,
        timeout_ms: Optional[int] = None,
    ) -> Optional[Decimal]:
        resp = await self.safe_request(
            provider=provider,
            method="GET",
            path="/payment/account",
            timeout_ms=timeout_ms,
            log_event="balance_failed",
        )
        if resp is None or resp.status_code != 200:
            return None
        envelope = self.json_or_none(resp) or {}
        raw_balance = envelope.get("balance")
        # Bitzone returns balance as int in smallest USDT units (1e-6).
        # Convert to a USDT decimal.
        balance = self.safe_decimal(raw_balance)
        if balance is None:
            return None
        return balance / Decimal("1000000")

    # ─── Provider-specific helpers ──────────────────────────────
    # _method_value / _bank_code are inherited from base as
    # resolve_method_value / resolve_bank_code.

    def parse_payin_response(
        self,
        *,
        provider: CascadeProvider,
        resp: httpx.Response,
        order_data: Dict[str, Any],
        fallback_method: PaymentMethod,
    ) -> IssueResult:
        envelope = self.parse_envelope_or_refusal(
            resp, bad_response_message="Bitzone pay-in: invalid JSON"
        )
        if isinstance(envelope, ProviderRefusal):
            return envelope

        trade_id = envelope.get("id")
        if not trade_id:
            return ProviderRefusal(
                code="bad_response",
                message="Bitzone pay-in: missing id",
                raw=envelope,
            )

        requisite = envelope.get("requisite") or {}
        if not requisite:
            return ProviderRefusal(
                code="no_capacity",
                message="Bitzone: no requisite returned",
                raw=envelope,
            )

        method_raw = requisite.get("method") or envelope.get("method") or ""
        method_ours = self.resolve_payment_method_from(
            method_raw, default=fallback_method
        )

        # For SBP/qr the actual receiver detail lives in sbpNumber; for card
        # it lives in requisites. Prefer the populated one.
        account_number = (
            requisite.get("requisites")
            or requisite.get("sbpNumber")
            or ""
        )

        amount_fiat = (
            self.safe_decimal(envelope.get("fiatAmount"))
            or self.safe_decimal(order_data.get("amount"))
            or Decimal("0")
        )
        provider_rate = self.safe_decimal(envelope.get("currencyRate"))

        return ProviderRequisiteResponse(
            external_order_id=str(trade_id),
            bank_name=str(requisite.get("bank") or envelope.get("bank") or ""),
            account_number=str(account_number),
            # Blank when absent — never the provider name (would leak the
            # provider / cascade to the merchant).
            account_holder=str(requisite.get("ownerName") or ""),
            payment_method=method_ours,
            payment_option_code=requisite.get("bank") or envelope.get("bank"),
            amount_fiat=amount_fiat,
            expires_at=self.default_expires_at(provider),
            raw=envelope,
            provider_rate=provider_rate,
        )

    async def _upload_invoice(
        self,
        *,
        provider: CascadeProvider,
        external_order_id: str,
        file_path: Optional[str],
    ) -> Optional[str]:
        """Upload a file to /file/trading/pay-in/invoice/upload.

        Bitzone requires ``tradeId`` in the query string (not the body), so
        we pass it via ``params=``. Returns the ``invoiceKey`` from the
        response on 200/201, ``None`` otherwise.
        """
        if not file_path:
            return None

        resp = await self.upload_file(
            provider=provider,
            method="POST",
            path="/file/trading/pay-in/invoice/upload",
            file_path=file_path,
            file_field="file",
            params={"tradeId": external_order_id},
            timeout_ms=provider.request_timeout_ms,
            log_event="invoice_upload_failed",
        )
        if resp is None or resp.status_code not in (200, 201):
            return None
        envelope = self.json_or_none(resp)
        if not isinstance(envelope, dict):
            return None
        key = envelope.get("invoiceKey") or envelope.get("key")
        return str(key) if key else None

    async def _fetch_fiat_amount(
        self, provider: CascadeProvider, external_order_id: str
    ) -> Optional[float]:
        """Fetch the trade's fiatAmount — required by Bitzone's dispute body."""
        resp = await self.safe_request(
            provider=provider,
            method="GET",
            path=f"/payment/trading/{external_order_id}",
            timeout_ms=provider.request_timeout_ms,
            log_event="dispute_fetch_amount_failed",
        )
        if resp is None or resp.status_code != 200:
            return None
        envelope = self.json_or_none(resp) or {}
        amount = self.safe_decimal(envelope.get("fiatAmount"))
        return float(amount) if amount is not None else None
