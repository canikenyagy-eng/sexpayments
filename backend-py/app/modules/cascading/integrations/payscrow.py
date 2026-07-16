"""Payscrow cascade adapter.

Docs: https://docs.payscrow-cascade.io/

Architecturally Payscrow is unusual:
  * The provider exposes **terminals** instead of a single merchant
    account — each terminal is one currency (RUB / UZS / AZN / …) and one
    API key. We treat one ``CascadeProvider`` row as one terminal.
  * **Auth** — a single ``X-API-Key`` header (no prefix, no signing).
  * **Webhooks** — Payscrow replays the same ``X-API-Key`` value on each
    webhook (no HMAC). We use the base's ``WEBHOOK_TOKEN_HEADER`` knob
    to switch verification into token-compare mode.
  * **Cancel / receipt** — there is no explicit endpoint for either.
    Orders auto-cancel on timeout, and Payscrow's trader-side flow
    confirms payment without a "client uploaded a receipt" call. Both
    ``cancel_request`` and ``notify_receipt`` are no-ops.
  * **Dispute** — multipart POST with an ``files[]`` array supporting
    several attachments per request. We use the base's ``upload_files``
    helper. Dispute body needs a fiat ``amount`` we don't carry on the
    raise_dispute signature, so we fetch it from the order.
  * **No provider rate** — the response carries ``fee`` (% commission)
    but no exchange rate. Set ``supports_provider_rate = False`` and let
    the cascade compute rate from RateConfig.

Status mapping (Payscrow → ProviderStatus):
  Unpaid              → PENDING  (created, awaiting payment)
  Completed           → SUCCESS  (final, money delivered)
  CanceledByTimeout   → EXPIRED  (final, automatic timeout)
  CanceledByService   → CANCELED (final, manual operator cancel)

Per-provider settings (``CascadeProvider.settings``):
  default_currency        — fiat currency of the terminal (RUB / UZS / …).
                            Used as the human-readable currency on the
                            ProviderRequisiteResponse only; Payscrow
                            doesn't echo currency back in the order body.
  unique_amount           — when True, requests "unique amount" trick
                            (Payscrow shifts the amount by 0..9 to reach
                            a unique value for higher conversion).
  expires_default_seconds — fallback TTL when ``expires_at`` isn't in
                            the response (it usually is — see field name).
  method_type_map         — our PaymentMethod → Payscrow ``method_type``
                            string. Empty = sbp→SBP, card→BankCard.
  nspk_code_map           — our PaymentOption.code → Payscrow nspk_code
                            (e.g. sber → bank100000000001). Optional;
                            pass-through identity when missing entries.
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


class PayscrowAdapter(ProviderAdapter):
    code = "payscrow"
    display_name = "Payscrow"
    description = (
        "P2P-площадка api.payscrow-cascade.io. Поддерживает RUB / UZS / AZN "
        "(терминал = валюта + регион). Авторизация — один X-API-Key. "
        "Курс провайдером не возвращается — используется наш RateConfig."
    )
    supports_provider_rate = False  # Payscrow doesn't echo a rate

    SUPPORTED_METHODS = (PaymentMethod.SBP, PaymentMethod.CARD)

    # ─── Signing knobs ───
    AUTH_HEADER = "X-API-Key"
    AUTH_SCHEME = ""
    SIGN_REQUESTS = False
    IDEMPOTENCY_HEADER = ""  # client_order_id in body is the idempotency key
    # Webhook auth — Payscrow replays the same X-API-Key on each callback.
    WEBHOOK_SECRET_SOURCE = "api_secret"
    WEBHOOK_TOKEN_HEADER = "X-API-Key"

    # ─── Аутентификация: один X-API-Key на терминал ───
    CREDENTIALS_SCHEMA = (
        AdapterFieldSpec(
            key="api_secret",
            label="X-API-Key (терминальный)",
            type="string",
            secret=True,
            required=True,
            description=(
                "Уникальный ключ терминала из админки Payscrow. "
                "Шлётся в X-API-Key на каждом запросе. Этот же ключ "
                "Payscrow реплеит обратно в X-API-Key на webhook'ах — "
                "верификация token-compare, отдельный webhook secret "
                "не нужен."
            ),
        ),
    )

    # ─── Declarative method / bank mappings ───
    DEFAULT_METHOD_MAP = {
        PaymentMethod.SBP.value: "SBP",
        PaymentMethod.CARD.value: "BankCard",
    }
    METHOD_MAP_SETTING_KEY = "method_type_map"
    # Payscrow's ``method_type`` reverse lookup. Covers their aggregate
    # types; specific NSPK / bank codes (returned via ``method_name``) are
    # ignored here — fallback_method handles them.
    PROVIDER_METHOD_TO_OURS = {
        "SBP": PaymentMethod.SBP,
        "TransSBP": PaymentMethod.SBP,
        "NSPKLink": PaymentMethod.SBP,
        "BankCard": PaymentMethod.CARD,
        "TransBankCard": PaymentMethod.CARD,
        "BankAccount": PaymentMethod.CARD,
    }
    # NSPK codes are opaque ("bank100000000004") — never auto-derive from
    # our payment_option_code. Admin must populate ``nspk_code_map`` if
    # they want bank-specific requests.
    BANK_CODE_TRANSFORM = "identity"
    BANK_MAP_SETTING_KEY = "nspk_code_map"
    REQUIRED_CREDENTIALS = ("api_secret",)

    PROVIDER_STATUS_MAP = {
        "Unpaid": ProviderStatus.PENDING,
        "Completed": ProviderStatus.SUCCESS,
        "CanceledByTimeout": ProviderStatus.EXPIRED,
        "CanceledByService": ProviderStatus.CANCELED,
    }

    SETTINGS_SCHEMA = (
        AdapterFieldSpec(
            key="default_currency",
            label="Валюта терминала",
            type="select",
            default="RUB",
            options=[
                AdapterFieldOption("RUB", "RUB"),
                AdapterFieldOption("UZS", "UZS"),
                AdapterFieldOption("AZN", "AZN"),
                AdapterFieldOption("EUR", "EUR"),
                AdapterFieldOption("KZT", "KZT"),
                AdapterFieldOption("TJS", "TJS"),
            ],
            description=(
                "Валюта, на которую настроен Payscrow-терминал. Используется "
                "только для отображения в админке (Payscrow в ответе валюту "
                "возвращает сам)."
            ),
        ),
        AdapterFieldSpec(
            key="unique_amount",
            label="Уникализировать сумму",
            type="boolean",
            default=True,
            description=(
                "Подбирать реквизит в диапазоне amount..amount+9 для повышения "
                "конверсии. Рекомендуется True для «круглых» сумм."
            ),
        ),
        AdapterFieldSpec(
            key="expires_default_seconds",
            label="TTL реквизита (сек) — fallback",
            type="number",
            default=1800,
            min=60,
            max=86400,
            description=(
                "Используется, только если в ответе нет expires_at "
                "(Payscrow обычно его присылает)."
            ),
        ),
        AdapterFieldSpec(
            key="method_type_map",
            label="Маппинг методов (наш → Payscrow method_type)",
            type="kv_map",
            value_type="select",
            options=[
                AdapterFieldOption("SBP", "SBP"),
                AdapterFieldOption("BankCard", "BankCard"),
                AdapterFieldOption("TransSBP", "TransSBP (СНГ)"),
                AdapterFieldOption("TransBankCard", "TransBankCard (СНГ)"),
                AdapterFieldOption("BankAccount", "BankAccount"),
                AdapterFieldOption("NSPKLink", "NSPKLink (юрлица/ИП)"),
            ],
            description=(
                "Closed whitelist при заполнении. Ключи — наши коды "
                "(sbp/card), значения — что слать в method_type. "
                "Пусто = sbp→SBP, card→BankCard."
            ),
        ),
        AdapterFieldSpec(
            key="nspk_code_map",
            label="Маппинг банков (наш PaymentOption.code → NSPK код)",
            type="kv_map",
            value_type="string",
            description=(
                "Опционально. Ключ — наш PaymentOption.code (sber, tinkoff), "
                "значение — Payscrow nspk_code (например bank100000000004). "
                "Если для какого-то банка код не задан, ордер создастся "
                "по агрегатному method_type без привязки к конкретному банку."
            ),
        ),
    )

    # ─── ProviderAdapter contract ──────────────────────────────
    # supports() / issue_requisite() inherited from base.

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
            "client_order_id": (
                order_data.get("merchant_request_id") or idempotency_key
            ),
            "order_side": "Buy",
            "method_type": method_value,
            "amount": self.format_amount(order_data["amount"]),
            "user_id": str(
                order_data.get("client_user_id") or idempotency_key
            ),
        }
        if "unique_amount" in settings:
            body["unique_amount"] = bool(settings.get("unique_amount"))
        # ``customer_name`` is optional; only send when caller passed one
        # — Payscrow's docs explicitly discourage faking it.
        customer_name = order_data.get("client_full_name")
        if customer_name:
            body["customer_name"] = str(customer_name)

        nspk = self.resolve_bank_code(provider, order_data.get("payment_option_code"))
        if nspk:
            body["nspk_code"] = nspk

        return PayinRequest(
            http_method="POST",
            path="/api/v1/order/",
            body=body,
        )

    def parse_payin_response(
        self,
        *,
        provider: CascadeProvider,
        resp: httpx.Response,
        order_data: Dict[str, Any],
        fallback_method: PaymentMethod,
    ) -> IssueResult:
        # Documented status code map (from Payscrow docs):
        #   500 "No available traders..." → no_capacity (NOT an error)
        #   402 "Insufficient balance"    → misconfigured-ish, log and refuse
        #   429                            → rate_limited (already covered)
        if resp.status_code == 500:
            return ProviderRefusal(
                code="no_capacity",
                message="Payscrow: no traders for these parameters",
                raw={"status": 500, "body": resp.text[:200]},
            )
        if resp.status_code == 402:
            return ProviderRefusal(
                code="insufficient_balance",
                message="Payscrow: insufficient terminal balance",
                raw={"status": 402, "body": resp.text[:200]},
            )

        envelope = self.parse_envelope_or_refusal(
            resp,
            ok_statuses=(200, 201),
            bad_response_message="Payscrow order: invalid JSON",
        )
        if isinstance(envelope, ProviderRefusal):
            return envelope

        order_id = envelope.get("id")
        if not order_id:
            return ProviderRefusal(
                code="bad_response",
                message="Payscrow: missing order id",
                raw=envelope,
            )

        amount_fiat = (
            self.safe_decimal(envelope.get("amount"))
            or self.safe_decimal(order_data.get("amount"))
            or Decimal("0")
        )

        # Payscrow doesn't return method_type on the order, only method_name.
        # We can still try a reverse lookup if it's listed; otherwise stick
        # to the caller's expected method.
        method_ours = self.resolve_payment_method_from(
            envelope.get("method_type"), default=fallback_method
        )

        expires_at = self.parse_iso8601(envelope.get("expires_at"))
        if expires_at is None:
            expires_at = self.default_expires_at(provider)

        return ProviderRequisiteResponse(
            external_order_id=str(order_id),
            bank_name=str(envelope.get("method_name") or ""),
            account_number=str(envelope.get("holder_account") or ""),
            # Blank when absent — never the provider name (would leak the
            # provider / cascade to the merchant).
            account_holder=str(envelope.get("holder_name") or ""),
            payment_method=method_ours,
            payment_option_code=envelope.get("method_name"),
            amount_fiat=amount_fiat,
            expires_at=expires_at,
            raw=envelope,
            provider_rate=None,
        )

    async def cancel_request(
        self,
        *,
        provider: CascadeProvider,
        external_order_id: str,
        timeout_ms: int,
    ) -> bool:
        """Payscrow doesn't expose a cancel endpoint — orders fall off
        on their own via ``CanceledByTimeout``. Returning True keeps the
        race-cancel branch happy without a network call.
        """
        return True

    async def notify_receipt(
        self,
        *,
        provider: CascadeProvider,
        external_order_id: str,
        receipt_path: str,
        comment: Optional[str],
    ) -> bool:
        """Payscrow's trader-side flow confirms payments — there is no
        client-side "I paid, here's my receipt" endpoint. We return True
        without doing anything; status flips via the webhook.
        """
        return True

    async def raise_dispute(
        self,
        *,
        provider: CascadeProvider,
        external_order_id: str,
        reason: str,
        evidence_paths: List[str],
    ) -> bool:
        """POST /api/v1/disputes/create with multipart files[].

        ``amount`` is required by Payscrow but isn't on our raise_dispute
        signature. We fetch the original order's fiat amount and forward
        that as the disputed amount.
        """
        amount = await self._fetch_order_amount(provider, external_order_id)
        if amount is None:
            logger.warning(
                "payscrow_dispute_no_amount",
                provider_id=provider.id,
                external_order_id=external_order_id,
            )
            return False

        form_data = {
            "order_id": external_order_id,
            "amount": self.format_amount(amount),
        }
        resp = await self.upload_files(
            provider=provider,
            method="POST",
            path="/api/v1/disputes/create",
            file_paths=evidence_paths or [],
            file_field="files",
            form_data=form_data,
            timeout_ms=provider.request_timeout_ms,
            log_event="dispute_failed",
        )
        return resp is not None and resp.status_code in (200, 201)

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
        # Payscrow nests the actual order in payload.payload.
        order = payload.get("payload") or {}
        external_id = order.get("id")
        if not external_id:
            raise CallbackVerificationError(
                "Payscrow webhook: missing payload.id"
            )

        status = self.parse_provider_status(order.get("status"))

        paid_amount: Optional[Decimal] = None
        if status in (ProviderStatus.SUCCESS, ProviderStatus.PAID):
            paid_amount = self.safe_decimal(order.get("amount"))

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
            path=f"/api/v1/order/{external_order_id}",
            timeout_ms=timeout_ms,
            log_event="poll_failed",
        )
        if resp is None or resp.status_code != 200:
            return None
        envelope = self.json_or_none(resp) or {}
        order = envelope.get("order") or {}
        status = self.try_parse_provider_status(order.get("status"))
        if status is None:
            return None
        return ParsedCallback(
            external_order_id=str(order.get("id") or external_order_id),
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
            path="/api/v1/finance/balance",
            timeout_ms=timeout_ms,
            log_event="balance_failed",
        )
        if resp is None or resp.status_code != 200:
            return None
        envelope = self.json_or_none(resp) or {}
        deposit = envelope.get("deposit") or {}
        # ``available`` is the spendable balance; ``total`` includes frozen.
        # We surface ``available`` so admins see what they can actually use
        # to back new orders.
        return self.safe_decimal(deposit.get("available"))

    # ─── Provider-specific helpers ──────────────────────────────

    async def _fetch_order_amount(
        self, provider: CascadeProvider, external_order_id: str
    ) -> Optional[Decimal]:
        """Pull ``order.amount`` from GET /order/{id} for dispute body."""
        resp = await self.safe_request(
            provider=provider,
            method="GET",
            path=f"/api/v1/order/{external_order_id}",
            timeout_ms=provider.request_timeout_ms,
            log_event="dispute_fetch_amount_failed",
        )
        if resp is None or resp.status_code != 200:
            return None
        envelope = self.json_or_none(resp) or {}
        order = envelope.get("order") or {}
        return self.safe_decimal(order.get("amount"))
