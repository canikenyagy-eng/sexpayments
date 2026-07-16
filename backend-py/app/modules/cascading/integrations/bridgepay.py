"""BridgePay cascade adapter.

Docs: api.blacklemon.pro Merchant API (BlackLemon / BridgePay platform).

Auth scheme — dual credential, two distinct headers:
  * ``X-Identity``  = API-key from the merchant cabinet (CascadeProvider.api_key_encrypted)
  * ``X-Signature`` = base64(HMAC-SHA1(secret, METHOD + FULL_URL + RAW_BODY))
                      where ``secret`` is CascadeProvider.api_secret_encrypted.
                      For GET and multipart requests, RAW_BODY is omitted.

There is **no timestamp** in the signature. The full URL (including
``https://host``) is part of the signing string — we read it from
``provider.base_url`` inside our overridden ``sign_request``.

Webhook verification is a **bare token compare** — provider replays whatever
``notificationToken`` we sent on invoice creation back in the
``X-Notification-Token`` header. Not HMAC. We store the token as the provider's
``webhook_secret_encrypted`` so credentials rotate together.

The provider exposes both pay-in (issue requisite) and pay-out, plus an account
balance endpoint that returns a list — we filter for USDT.

Per-provider settings:
  notification_url        — public URL of our cascade callback (sent on every invoice)
  default_currency        — store currency (RUB by default)
  default_payment_option  — what to send when the merchant pays by card and we
                            don't have a specific opt code
  payment_method_map      — our PaymentOption.code → BridgePay paymentMethod
                            (sber → sberbank, tinkoff → tinkoff, etc).
                            Empty = identity mapping.
  payment_option_map      — our PaymentMethod → BridgePay paymentOption
                            (sbp → SBP, card → TO_CARD, sim → MOBILE_TOP_UP).
                            Pass-through whitelist when set.
  dispute_reason_default  — what to send as disputeReason; default "no_payment".
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from decimal import Decimal
from typing import Any, Dict, List, Mapping, Optional

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


class BridgePayAdapter(ProviderAdapter):
    code = "bridgepay"
    display_name = "BridgePay"
    description = (
        "P2P-площадка BridgePay / BlackLemon (api.blacklemon.pro). "
        "Host-to-Host: реквизиты выдаются сразу при создании инвойса со "
        "startDeal=true. Курс — из ответа провайдера (deals[0].rate)."
    )
    supports_provider_rate = True

    SUPPORTED_METHODS = (
        PaymentMethod.SBP,
        PaymentMethod.CARD,
        PaymentMethod.SIM,
    )

    # ─── Signing knobs ───
    # We never use the base sign_request body because BridgePay signs the
    # *raw* JSON body (not canonical) plus the *full URL* (not just path) —
    # we have to override sign_request entirely. The knobs below are still
    # used by ``compute_signature`` so the override stays declarative.
    SIGNATURE_ALGORITHM = "sha1"
    SIGNATURE_ENCODING = "base64"
    AUTH_HEADER = "X-Identity"
    AUTH_SCHEME = ""  # X-Identity carries the api_key as-is, no scheme prefix.
    TIMESTAMP_HEADER = ""  # no timestamp in this scheme
    SIGNATURE_HEADER = "X-Signature"
    # BridgePay's idempotency lives on the internalId in the body — no header.
    IDEMPOTENCY_HEADER = ""

    # Webhook header name — different from the request signature. Token-compare
    # mode is driven by the base ``verify_callback_signature`` via this knob.
    WEBHOOK_TOKEN_HEADER = "X-Notification-Token"
    # Kept as an alias for backwards-compat with any external code that
    # referenced this attribute directly.
    NOTIFICATION_TOKEN_HEADER = WEBHOOK_TOKEN_HEADER

    # ─── Аутентификация: dual-credential + notification token ───
    CREDENTIALS_SCHEMA = (
        AdapterFieldSpec(
            key="api_key",
            label="X-Identity (API key)",
            type="string",
            secret=True,
            required=True,
            description=(
                "Идентификатор мерчанта из кабинета BlackLemon. Шлётся "
                "в заголовке X-Identity на каждом запросе."
            ),
        ),
        AdapterFieldSpec(
            key="api_secret",
            label="X-Signature secret (HMAC-SHA1 key)",
            type="string",
            secret=True,
            required=True,
            description=(
                "Секрет для HMAC-SHA1 подписи METHOD+FULL_URL+RAW_BODY. "
                "Подпись base64 шлётся в X-Signature."
            ),
        ),
        AdapterFieldSpec(
            key="webhook_secret",
            label="Notification token (X-Notification-Token)",
            type="string",
            secret=True,
            description=(
                "Pre-shared token. BridgePay реплеит его обратно в "
                "X-Notification-Token на каждом webhook'е — мы сверяем "
                "байт-в-байт без HMAC."
            ),
        ),
    )

    # ─── Declarative method/bank mappings ───
    # CARD has dynamic resolution (settings.default_payment_option) handled
    # by an override of resolve_method_value below — DEFAULT_METHOD_MAP
    # covers the static SBP / SIM cases.
    DEFAULT_METHOD_MAP = {
        PaymentMethod.SBP.value: "SBP",
        PaymentMethod.CARD.value: "TO_CARD",
        PaymentMethod.SIM.value: "MOBILE_TOP_UP",
    }
    METHOD_MAP_SETTING_KEY = "payment_option_map"
    PROVIDER_METHOD_TO_OURS = {
        "SBP": PaymentMethod.SBP,
        "SBP_QR": PaymentMethod.SBP,
        "TO_CARD": PaymentMethod.CARD,
        "TO_ACCOUNT": PaymentMethod.CARD,
        "TO_BANK_DETAILS": PaymentMethod.CARD,
        "MOBILE_TOP_UP": PaymentMethod.SIM,
        "CROSS_BORDER": PaymentMethod.CARD,
        "MANUAL_SBP_QR": PaymentMethod.SBP,
    }
    BANK_CODE_TRANSFORM = "identity"  # passthrough for BridgePay paymentMethod
    BANK_MAP_SETTING_KEY = "payment_method_map"
    REQUIRED_CREDENTIALS = ("api_key", "api_secret")

    PROVIDER_STATUS_MAP = {
        # Invoice / deal statuses observed in the docs and webhook samples.
        "new": ProviderStatus.PENDING,
        "transfer_waiting": ProviderStatus.PENDING,
        "transfer_confirmed": ProviderStatus.PAID,
        "paid": ProviderStatus.SUCCESS,
        "completed": ProviderStatus.SUCCESS,
        "success": ProviderStatus.SUCCESS,
        "dispute": ProviderStatus.DISPUTED,
        "canceled": ProviderStatus.CANCELED,
        "cancelled": ProviderStatus.CANCELED,
        "expired": ProviderStatus.EXPIRED,
    }

    SETTINGS_SCHEMA = (
        AdapterFieldSpec(
            key="notification_url",
            label="URL коллбэка (отправляется в каждом инвойсе)",
            type="url",
            description=(
                "Передаётся в notificationUrl при создании инвойса — на этот URL "
                "BridgePay шлёт обновления статусов. Должно совпадать с нашим "
                "/api/cascade/v1/callbacks/<provider_code>."
            ),
            placeholder="https://example.com/api/cascade/v1/callbacks/bridgepay",
        ),
        AdapterFieldSpec(
            key="default_currency",
            label="Валюта магазина",
            type="string",
            default="RUB",
            placeholder="RUB",
        ),
        AdapterFieldSpec(
            key="default_payment_option",
            label="Default paymentOption для CARD",
            type="select",
            default="TO_CARD",
            options=[
                AdapterFieldOption("TO_CARD", "TO_CARD"),
                AdapterFieldOption("TO_ACCOUNT", "TO_ACCOUNT"),
                AdapterFieldOption("TO_BANK_DETAILS", "TO_BANK_DETAILS"),
            ],
            description=(
                "Какой paymentOption отправлять, когда наш payment_method = CARD "
                "и нет явной привязки в payment_option_map. SBP/SIM маппятся "
                "автоматически."
            ),
        ),
        AdapterFieldSpec(
            key="payment_method_map",
            label="Маппинг банков (наш PaymentOption.code → BridgePay paymentMethod)",
            type="kv_map",
            value_type="string",
            description=(
                "Ключ — наш PaymentOption.code (напр. sber), значение — "
                "BridgePay paymentMethod (sberbank). Пусто = passthrough."
            ),
        ),
        AdapterFieldSpec(
            key="payment_option_map",
            label="Маппинг методов (наш PaymentMethod → BridgePay paymentOption)",
            type="kv_map",
            value_type="select",
            options=[
                AdapterFieldOption("SBP", "SBP"),
                AdapterFieldOption("SBP_QR", "SBP_QR"),
                AdapterFieldOption("TO_CARD", "TO_CARD"),
                AdapterFieldOption("TO_ACCOUNT", "TO_ACCOUNT"),
                AdapterFieldOption("MOBILE_TOP_UP", "MOBILE_TOP_UP"),
            ],
            description=(
                "Если задано — closed whitelist. Пусто = sbp→SBP, card→TO_CARD, "
                "sim→MOBILE_TOP_UP."
            ),
        ),
        AdapterFieldSpec(
            key="dispute_reason_default",
            label="Причина спора по умолчанию",
            type="select",
            default="no_payment",
            options=[
                AdapterFieldOption("no_payment", "no_payment"),
                AdapterFieldOption("invalid_sum", "invalid_sum"),
                AdapterFieldOption("other", "other"),
            ],
        ),
    )

    # ─── Custom signing: X-Identity + X-Signature(METHOD + FULL_URL + RAW_BODY) ───

    def sign_request(
        self,
        *,
        token: str,
        method: str,
        path: str,
        body: Optional[Mapping[str, Any]] = None,
        idempotency_key: Optional[str] = None,
        extra_headers: Optional[Mapping[str, str]] = None,
        provider: Optional[CascadeProvider] = None,
    ) -> Dict[str, str]:
        if provider is None:
            raise CallbackVerificationError(
                "BridgePay sign_request requires the provider (base_url + api_key)"
            )
        api_key = self.get_api_key(provider)
        if not api_key:
            raise CallbackVerificationError(
                "BridgePay provider has no api_key configured — set X-Identity in admin"
            )

        body_str = self._body_for_signing(body, method)
        full_url = self._full_url(provider, path)
        signing_payload = f"{method.upper()}{full_url}{body_str}"

        digest = hmac.new(
            token.encode("utf-8"),
            signing_payload.encode("utf-8"),
            hashlib.sha1,
        ).digest()
        signature = base64.b64encode(digest).decode("ascii")

        headers: Dict[str, str] = {
            "X-Identity": api_key,
            "X-Signature": signature,
        }
        if extra_headers:
            headers.update(extra_headers)
        # idempotency_key intentionally unused — BridgePay's idempotency lives
        # on the internalId we put into the invoice body.
        _ = idempotency_key
        return headers

    @staticmethod
    def _full_url(provider: CascadeProvider, path: str) -> str:
        base = (provider.base_url or "").rstrip("/")
        if not path.startswith("/"):
            path = "/" + path
        return f"{base}{path}"

    def _body_for_signing(
        self, body: Optional[Mapping[str, Any]], method: str
    ) -> str:
        """Render the request body the way BridgePay expects to sign it.

        - GET           → "" (per docs)
        - multipart     → "" (provider doesn't sign multipart bodies)
        - JSON body     → exactly the bytes the transport will ship. We route
                          through ``serialize_request_body`` so the base
                          ``request_signed`` and the signing path use one
                          source of truth.
        """
        if body is None or method.upper() == "GET":
            return ""
        return self.serialize_request_body(body)

    def serialize_request_body(self, body: Optional[Mapping[str, Any]]) -> str:
        """BridgePay doesn't require sort_keys — just compact JSON.

        Insertion order matches the Python dict we built in issue_requisite,
        which is also what the documentation samples use. The important bit is
        that ``request_signed`` and our overridden ``sign_request`` share this
        method so transport bytes == signing bytes.
        """
        if body is None:
            return ""
        return json.dumps(body, separators=(",", ":"), ensure_ascii=False)

    # ─── Webhook ───
    # verify_callback_signature inherited from base — it sees
    # WEBHOOK_TOKEN_HEADER above and switches to token-compare mode.

    # ─── ProviderAdapter contract ──────────────────────────────
    # supports() / issue_requisite() inherited from base.

    def resolve_method_value(
        self, provider: CascadeProvider, method: PaymentMethod
    ) -> Optional[str]:
        """Override for the CARD branch where ``default_payment_option``
        (TO_CARD / TO_ACCOUNT / TO_BANK_DETAILS) can be picked per provider.
        """
        # Custom whitelist takes precedence as always.
        option_map = self.get_settings(provider).get(self.METHOD_MAP_SETTING_KEY)
        if option_map:
            return option_map.get(method.value)
        if method == PaymentMethod.CARD:
            return self.get_settings(provider).get(
                "default_payment_option",
                self.DEFAULT_METHOD_MAP.get(method.value),
            )
        return self.DEFAULT_METHOD_MAP.get(method.value)

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
            "type": "in",
            "amount": self.format_amount(order_data["amount"]),
            "currency": settings.get("default_currency", "RUB"),
            "notificationUrl": (
                settings.get("notification_url")
                or self.build_cascade_callback_url(provider.code)
            ),
            "notificationToken": self.get_webhook_secret(provider) or "",
            "internalId": order_data.get("merchant_request_id") or idempotency_key,
            "userId": str(order_data.get("client_user_id") or ""),
            "paymentOption": method_value,
            "paymentMethod": self.resolve_bank_code(
                provider, order_data.get("payment_option_code")
            ),
            "startDeal": True,
        }
        return PayinRequest(
            http_method="POST",
            path="/api/merchant/invoices",
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
            path=f"/api/merchant/invoices/{external_order_id}/cancel",
            body=None,  # body-less POST; signature payload empty
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
        """BridgePay's receipt endpoint = ``/confirm-transfer`` (multipart).

        Multipart requests are signed only over METHOD + FULL_URL (no body).
        """
        if not receipt_path:
            return True
        resp = await self.upload_file(
            provider=provider,
            method="POST",
            path=f"/api/merchant/invoices/{external_order_id}/confirm-transfer",
            file_path=receipt_path,
            file_field="attachment",
            timeout_ms=provider.request_timeout_ms,
            log_event="receipt_upload_failed",
        )
        return resp is not None and resp.status_code == 200

    async def raise_dispute(
        self,
        *,
        provider: CascadeProvider,
        external_order_id: str,
        reason: str,
        evidence_paths: List[str],
    ) -> bool:
        """BridgePay disputes are keyed by ``dealId``, not the invoice id, so we
        first GET the invoice to find the active deal."""
        deal_id = await self._fetch_active_deal_id(provider, external_order_id)
        if deal_id is None:
            logger.warning(
                "bridgepay_dispute_no_deal",
                provider_id=provider.id,
                external_order_id=external_order_id,
            )
            return False

        reason_code = (
            reason
            if reason in {"no_payment", "invalid_sum", "other"}
            else self.get_settings(provider).get("dispute_reason_default", "no_payment")
        )

        evidence = evidence_paths or [None]
        ok = True
        for path_opt in evidence:
            if not await self._submit_dispute(
                provider=provider,
                external_order_id=external_order_id,
                deal_id=deal_id,
                reason=reason_code,
                evidence_path=path_opt,
            ):
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
        # BridgePay's webhook body is an InvoiceDTO. The id we care about is
        # the invoice id (matches what we stored as external_order_id).
        invoice_id = payload.get("id")
        if not invoice_id:
            raise CallbackVerificationError("Webhook missing invoice id")

        status_raw = payload.get("status")
        status = self.parse_provider_status(status_raw)

        paid_amount: Optional[Decimal] = None
        if status in (ProviderStatus.SUCCESS, ProviderStatus.PAID):
            paid_amount = self.safe_decimal((payload.get("sum") or {}).get("amount"))

        return ParsedCallback(
            external_order_id=str(invoice_id),
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
            path=f"/api/merchant/invoices/{external_order_id}",
            body=None,
            timeout_ms=timeout_ms,
            log_event="poll_failed",
        )
        if resp is None or resp.status_code != 200:
            return None
        invoice = self.json_or_none(resp) or {}
        status = self.try_parse_provider_status(invoice.get("status"))
        if status is None:
            return None
        return ParsedCallback(
            external_order_id=str(invoice.get("id") or external_order_id),
            status=status,
            raw={"event": "polled", "envelope": invoice},
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
            path="/api/merchant/accounts",
            body=None,
            timeout_ms=timeout_ms,
            log_event="balance_failed",
        )
        if resp is None or resp.status_code != 200:
            return None
        accounts = self.json_or_none(resp)
        if not isinstance(accounts, list):
            return None
        for account in accounts:
            if not isinstance(account, dict) or account.get("currency") != "USDT":
                continue
            balance = self.safe_decimal((account.get("sum") or {}).get("amount"))
            if balance is not None:
                return balance
        return None

    # ─── Provider-specific helpers ─────────────────────────────
    # _payment_option / _payment_method inherited from base as
    # resolve_method_value (overridden above for CARD default fallback)
    # and resolve_bank_code.

    def parse_payin_response(
        self,
        *,
        provider: CascadeProvider,
        resp: httpx.Response,
        order_data: Dict[str, Any],
        fallback_method: PaymentMethod,
    ) -> IssueResult:
        invoice = self.parse_envelope_or_refusal(
            resp, bad_response_message="BridgePay invoice: invalid JSON"
        )
        if isinstance(invoice, ProviderRefusal):
            return invoice

        deals = invoice.get("deals") or []
        if not deals:
            return ProviderRefusal(
                code="no_capacity",
                message="BridgePay: no requisites available",
                raw=invoice,
            )

        deal = deals[0]
        requisites = deal.get("requisites") or {}
        invoice_id = invoice.get("id")
        if not invoice_id:
            return ProviderRefusal(
                code="bad_response",
                message="BridgePay invoice: missing id",
                raw=invoice,
            )

        amount_fiat = (
            self.safe_decimal((invoice.get("sum") or {}).get("amount"))
            or self.safe_decimal(order_data.get("amount"))
            or Decimal("0")
        )
        provider_rate = self.safe_decimal(deal.get("rate"))
        method_ours = self.resolve_payment_method_from(
            deal.get("paymentOption"), default=fallback_method
        )
        expires_at = self.parse_iso8601(invoice.get("expireAt")) or self.default_expires_at(
            provider, fallback_seconds=1800
        )

        bank_name = (
            deal.get("paymentMethodName")
            or deal.get("paymentMethod")
            or invoice.get("paymentMethod")
            or ""
        )

        return ProviderRequisiteResponse(
            external_order_id=str(invoice_id),
            bank_name=str(bank_name),
            account_number=str(requisites.get("requisites") or ""),
            # Blank when absent — never the provider name (would leak the
            # provider / cascade to the merchant).
            account_holder=str(requisites.get("holder") or ""),
            payment_method=method_ours,
            payment_option_code=deal.get("paymentMethod") or invoice.get("paymentMethod"),
            amount_fiat=amount_fiat,
            expires_at=expires_at,
            raw=invoice,
            provider_rate=provider_rate,
        )

    async def _fetch_active_deal_id(
        self, provider: CascadeProvider, invoice_id: str
    ) -> Optional[str]:
        resp = await self.safe_request(
            provider=provider,
            method="GET",
            path=f"/api/merchant/invoices/{invoice_id}",
            body=None,
            timeout_ms=provider.request_timeout_ms,
            log_event="fetch_invoice_failed",
        )
        if resp is None or resp.status_code != 200:
            return None
        invoice = self.json_or_none(resp) or {}
        deals = invoice.get("deals") or []
        for deal in deals:
            if deal.get("isActive"):
                return deal.get("id")
        # No active deal — fall back to first deal if any.
        if deals:
            return deals[0].get("id")
        return None

    async def _submit_dispute(
        self,
        *,
        provider: CascadeProvider,
        external_order_id: str,
        deal_id: str,
        reason: str,
        evidence_path: Optional[str],
    ) -> bool:
        form_data: Dict[str, Any] = {
            "dealId": deal_id,
            "disputeReason": reason,
        }
        resp = await self.upload_file(
            provider=provider,
            method="POST",
            path=f"/api/merchant/invoices/{external_order_id}/dispute",
            file_path=evidence_path,
            file_field="attachment",
            form_data=form_data,
            timeout_ms=provider.request_timeout_ms,
            log_event="dispute_failed",
        )
        return resp is not None and resp.status_code == 200
