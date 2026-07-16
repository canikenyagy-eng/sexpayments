"""Garex cascade adapter.

Docs: https://stage.garex.one/ (Merchant API)

Auth scheme matches base defaults — bearer token on every call, no body
signature, no timestamp:
  * outbound — ``Authorization: Bearer <token>`` (``SIGN_REQUESTS=False``).
  * inbound  — webhooks carry a ``sign`` field inside the JSON body. The
    docs we received don't describe the algorithm, so signature
    verification is left as a no-op until a scheme is provided. The
    ``sign`` value is exposed via ``ParsedCallback.raw`` for downstream
    audit. To start enforcing, drop ``WEBHOOK_SECRET_SOURCE`` and override
    ``verify_callback_signature``.

Dispute/receipt handling is **endpoint-unified**: there is no dedicated
multipart upload endpoint. Status changes (paid / dispute / canceled) all
flow through ``PATCH /api/merchant/payments/<id>/status`` with the optional
``file`` field carrying a base64 data URL of the proof. ``file_to_data_url``
in the base does the encoding lift.

Status mapping (Garex → ProviderStatus):
  created   → CREATED  (created, requisites not yet issued)
  pending   → PENDING  (requisites issued, awaiting payment)
  paid      → PAID     (payer reports paid — pending operator confirmation)
  finished  → SUCCESS  (operator confirmed)
  canceled  → CANCELED (final)
  dispute   → DISPUTED
  failed    → FAILED   (could not be created)

Per-provider settings (``CascadeProvider.settings``):
  merchant_id             — required. Sent in body of every pay-in/poll call.
  callback_url            — URL Garex POSTs status changes to (must match
                            ``/api/cascade/v1/callbacks/<provider_code>``).
  default_currency        — fiat currency code (RUB by default).
  method_map              — our PaymentMethod → Garex ``method`` value
                            (sbp/c2c/sim/m2tjs_sbp/...). Empty = sbp→sbp,
                            card→c2c, sim→sim.
  bank_code_map           — our PaymentOption.code → Garex ``assetOrBank``
                            (sber → sber, tinkoff → t-bank). Empty =
                            lowercase passthrough.
  expires_default_seconds — TTL used when Garex doesn't return an explicit
                            payment window (default 1800).
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


class GarexAdapter(ProviderAdapter):
    code = "garex"
    display_name = "Garex"
    description = (
        "P2P-площадка stage.garex.one. Поддерживает CARD (c2c), SBP (sbp), "
        "SIM (sim) и широкий набор трансграничных / bank-to-bank методов. "
        "Авторизация — Authorization: Bearer. Курс берётся из их ответа (rate)."
    )
    supports_provider_rate = True

    SUPPORTED_METHODS = (
        PaymentMethod.SBP,
        PaymentMethod.CARD,
        PaymentMethod.SIM,
    )

    # ─── Signing knobs ───
    # Standard bearer auth, no signing or timestamp on outbound. Garex's
    # orderId in the body is the idempotency key, so no header is sent.
    SIGN_REQUESTS = False
    IDEMPOTENCY_HEADER = ""

    # ─── Аутентификация ───
    CREDENTIALS_SCHEMA = (
        AdapterFieldSpec(
            key="api_secret",
            label="Bearer token",
            type="string",
            secret=True,
            required=True,
            description=(
                "Токен из кабинета Garex. Шлётся в Authorization: Bearer "
                "<token> на каждом запросе."
            ),
        ),
        AdapterFieldSpec(
            key="webhook_secret",
            label="Webhook secret (опционально)",
            type="string",
            secret=True,
            description=(
                "Garex не публиковал алгоритм подписи webhook'ов. Поле "
                "оставлено для будущего использования — если они "
                "введут scheme через ``sign`` field в body, мы здесь "
                "получим ключ для проверки."
            ),
        ),
    )

    # ─── Declarative method/bank mappings ───
    DEFAULT_METHOD_MAP = {
        PaymentMethod.SBP.value: "sbp",
        PaymentMethod.CARD.value: "c2c",
        PaymentMethod.SIM.value: "sim",
    }
    PROVIDER_METHOD_TO_OURS = {
        # plain
        "sbp": PaymentMethod.SBP,
        "c2c": PaymentMethod.CARD,
        "sim": PaymentMethod.SIM,
        # "white triangle"
        "sbp_wt": PaymentMethod.SBP,
        "c2c_wt": PaymentMethod.CARD,
        # cross-border SBP
        "m2tjs_sbp": PaymentMethod.SBP,
        "m2abh_sbp": PaymentMethod.SBP,
        "m2arm_sbp": PaymentMethod.SBP,
        # cross-border card
        "m2tjs_c2c": PaymentMethod.CARD,
        "m2abh_c2c": PaymentMethod.CARD,
        "m2arm_c2c": PaymentMethod.CARD,
        "m2geo_c2c": PaymentMethod.CARD,
        # phone-to-phone SBP-flavoured
        "sber2sber": PaymentMethod.SBP,
        "alfa2alfa": PaymentMethod.SBP,
        "vtb2vtb": PaymentMethod.SBP,
        "tbank2tbank": PaymentMethod.SBP,
        "ozon2ozon": PaymentMethod.SBP,
        # link / NSPK / misc
        "link2pay": PaymentMethod.CARD,
        "link2inter": PaymentMethod.CARD,
        "linkmt2tjs": PaymentMethod.CARD,
        "bank-account": PaymentMethod.CARD,
        "alfa-qr": PaymentMethod.SBP,
        "gos": PaymentMethod.CARD,
    }
    BANK_CODE_TRANSFORM = "lower"
    REQUIRED_SETTINGS = ("merchant_id", "callback_url")
    REQUIRED_CREDENTIALS = ("api_secret",)

    PROVIDER_STATUS_MAP = {
        "created": ProviderStatus.CREATED,
        "pending": ProviderStatus.PENDING,
        "paid": ProviderStatus.PAID,
        "finished": ProviderStatus.SUCCESS,
        "canceled": ProviderStatus.CANCELED,
        "dispute": ProviderStatus.DISPUTED,
        "failed": ProviderStatus.FAILED,
    }

    SETTINGS_SCHEMA = (
        AdapterFieldSpec(
            key="merchant_id",
            label="Merchant ID",
            type="string",
            required=True,
            description=(
                "Идентификатор мерчанта из личного кабинета Garex. Идёт в "
                "body каждого запроса payin/status."
            ),
            placeholder="mer123ch123bt123",
        ),
        AdapterFieldSpec(
            key="callback_url",
            label="Webhook URL (callbackUri)",
            type="url",
            required=True,
            description=(
                "Передаётся в каждом запросе как callbackUri — Garex шлёт "
                "обновления статусов на этот адрес. Укажите "
                "https://<host>/api/cascade/v1/callbacks/garex."
            ),
            placeholder="https://example.com/api/cascade/v1/callbacks/garex",
        ),
        AdapterFieldSpec(
            key="default_currency",
            label="Фиатная валюта по умолчанию",
            type="string",
            default="RUB",
            placeholder="RUB",
            description="RUB / EUR / AZN — что отправляется как currency.",
        ),
        AdapterFieldSpec(
            key="expires_default_seconds",
            label="TTL реквизита (сек)",
            type="number",
            default=1800,
            min=60,
            max=86400,
            description="Используется, если Garex не вернул явный таймаут.",
        ),
        AdapterFieldSpec(
            key="method_map",
            label="Маппинг методов (наш → Garex method)",
            type="kv_map",
            value_type="select",
            options=[
                AdapterFieldOption("sbp", "sbp"),
                AdapterFieldOption("c2c", "c2c"),
                AdapterFieldOption("sim", "sim"),
                AdapterFieldOption("sbp_wt", "sbp_wt (white triangle)"),
                AdapterFieldOption("c2c_wt", "c2c_wt (white triangle)"),
                AdapterFieldOption("m2tjs_sbp", "m2tjs_sbp (Таджикистан SBP)"),
                AdapterFieldOption("m2tjs_c2c", "m2tjs_c2c (Таджикистан карта)"),
                AdapterFieldOption("m2abh_sbp", "m2abh_sbp (Абхазия SBP)"),
                AdapterFieldOption("m2abh_c2c", "m2abh_c2c (Абхазия карта)"),
                AdapterFieldOption("m2arm_sbp", "m2arm_sbp (Армения SBP)"),
                AdapterFieldOption("m2arm_c2c", "m2arm_c2c (Армения карта)"),
                AdapterFieldOption("m2geo_c2c", "m2geo_c2c (Грузия карта, EUR)"),
                AdapterFieldOption("sber2sber", "sber2sber"),
                AdapterFieldOption("alfa2alfa", "alfa2alfa"),
                AdapterFieldOption("vtb2vtb", "vtb2vtb"),
                AdapterFieldOption("tbank2tbank", "tbank2tbank"),
                AdapterFieldOption("ozon2ozon", "ozon2ozon"),
                AdapterFieldOption("link2pay", "link2pay (НСПК)"),
                AdapterFieldOption("link2inter", "link2inter"),
                AdapterFieldOption("alfa-qr", "alfa-qr"),
                AdapterFieldOption("bank-account", "bank-account"),
                AdapterFieldOption("gos", "gos"),
            ],
            description=(
                "Closed whitelist при заполнении. Ключи — наши коды "
                "(sbp/card/sim), значения — что отправлять Garex в поле "
                "method. Пусто = sbp→sbp, card→c2c, sim→sim."
            ),
        ),
        AdapterFieldSpec(
            key="bank_code_map",
            label="Маппинг банков (наш PaymentOption.code → Garex assetOrBank)",
            type="kv_map",
            value_type="string",
            description=(
                "Ключ — наш PaymentOption.code (sber, tinkoff), значение — "
                "код банка для Garex (sber, t-bank). Пусто = lowercase "
                "нашего кода."
            ),
        ),
    )

    # ─── ProviderAdapter contract ──────────────────────────────
    # supports() / issue_requisite() inherited from base — uses the
    # declarative REQUIRED_SETTINGS + DEFAULT_METHOD_MAP knobs above.

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
        callback_url = (
            order_data.get("garex_callback_url")
            or settings.get("callback_url")
            or self.build_cascade_callback_url(provider.code)
        )
        body: Dict[str, Any] = {
            "orderId": order_data.get("merchant_request_id") or idempotency_key,
            "merchantId": str(settings["merchant_id"]),
            "method": method_value,
            "amount": float(Decimal(str(order_data["amount"]))),
            "currency": settings.get("default_currency", "RUB"),
            "userId": str(order_data.get("client_user_id") or idempotency_key),
            "callbackUri": str(callback_url),
        }
        bank = self.resolve_bank_code(provider, order_data.get("payment_option_code"))
        if bank:
            body["assetOrBank"] = bank
        user_ip = order_data.get("client_ip")
        if user_ip:
            body["userIp"] = str(user_ip)
        return PayinRequest(
            http_method="POST",
            path="/api/merchant/payments/payin",
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
        # Garex documents these failure codes:
        #   422 → orderId already exists (treat as misconfigured/retry-noop)
        #   404 → no matching offer
        #   400 → offer found but no free requisite (no_capacity)
        #   500 → internal failure
        if resp.status_code == 404:
            return ProviderRefusal(
                code="no_capacity",
                message="Garex: no matching offer for parameters",
                raw={"status": resp.status_code, "body": resp.text[:200]},
            )
        if resp.status_code == 400:
            return ProviderRefusal(
                code="no_capacity",
                message="Garex: no free requisite for this offer",
                raw={"status": resp.status_code, "body": resp.text[:200]},
            )

        envelope = self.parse_envelope_or_refusal(
            resp, bad_response_message="Garex pay-in: invalid JSON"
        )
        if isinstance(envelope, ProviderRefusal):
            return envelope

        if envelope.get("status") is False:
            return ProviderRefusal(
                code="no_capacity",
                message="Garex: status=false (no requisite available)",
                raw=envelope,
            )

        result = envelope.get("result")
        if not result or not isinstance(result, dict):
            return ProviderRefusal(
                code="no_capacity",
                message="Garex: result block missing",
                raw=envelope,
            )

        payment_id = result.get("id")
        if not payment_id:
            return ProviderRefusal(
                code="bad_response",
                message="Garex: missing payment id in result",
                raw=envelope,
            )

        method_ours = self.resolve_payment_method_from(
            envelope.get("method"), default=fallback_method
        )

        amount_fiat = (
            self.safe_decimal(result.get("amount"))
            or self.safe_decimal(order_data.get("amount"))
            or Decimal("0")
        )
        provider_rate = self.safe_decimal(result.get("rate"))

        return ProviderRequisiteResponse(
            external_order_id=str(payment_id),
            bank_name=str(result.get("bankName") or result.get("bank") or ""),
            account_number=str(result.get("address") or ""),
            # Blank when absent — never the provider name (would leak the
            # provider / cascade to the merchant).
            account_holder=str(result.get("recipient") or ""),
            payment_method=method_ours,
            payment_option_code=result.get("bank"),
            amount_fiat=amount_fiat,
            expires_at=self.default_expires_at(provider),
            raw=envelope,
            provider_rate=provider_rate,
        )

    async def cancel_request(
        self,
        *,
        provider: CascadeProvider,
        external_order_id: str,
        timeout_ms: int,
    ) -> bool:
        return await self._set_status(
            provider=provider,
            external_order_id=external_order_id,
            status="canceled",
            file_path=None,
            timeout_ms=timeout_ms,
            log_event="cancel_failed",
        )

    async def notify_receipt(
        self,
        *,
        provider: CascadeProvider,
        external_order_id: str,
        receipt_path: str,
        comment: Optional[str],
    ) -> bool:
        """Garex doesn't have a dedicated receipt endpoint — receipts go
        through PATCH status with status=paid + file=base64. If
        ``receipt_path`` is empty we still mark the order as paid (the
        operator-side flow will need to upload separately)."""
        return await self._set_status(
            provider=provider,
            external_order_id=external_order_id,
            status="paid",
            file_path=receipt_path or None,
            timeout_ms=provider.request_timeout_ms,
            log_event="receipt_forward_failed",
        )

    async def raise_dispute(
        self,
        *,
        provider: CascadeProvider,
        external_order_id: str,
        reason: str,
        evidence_paths: List[str],
    ) -> bool:
        """Dispute = PATCH status with status=dispute. Multiple evidence
        files cycle through the same endpoint; first one wins as the
        ``file`` attachment, the rest are uploaded with subsequent calls
        so each piece of evidence reaches Garex."""
        evidence = evidence_paths or [None]
        ok = True
        for path in evidence:
            if not await self._set_status(
                provider=provider,
                external_order_id=external_order_id,
                status="dispute",
                file_path=path,
                timeout_ms=provider.request_timeout_ms,
                log_event="dispute_failed",
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
        # Garex docs don't define a webhook signature algorithm; we expose
        # the body's ``sign`` field via .raw for downstream audit but skip
        # verification. If they publish a scheme later, set
        # WEBHOOK_SECRET_SOURCE + override verify_callback_signature.
        payload = self.verify_and_decode_callback(
            provider=provider, headers=headers, body=body
        )

        external_id = payload.get("id")
        if not external_id:
            raise CallbackVerificationError("Webhook missing id")

        status = self.parse_provider_status(payload.get("state"))

        paid_amount: Optional[Decimal] = None
        if status in (ProviderStatus.SUCCESS, ProviderStatus.PAID):
            paid_amount = self.safe_decimal(payload.get("amount"))

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
        merchant_id = self.get_settings(provider).get("merchant_id")
        if not merchant_id:
            return None
        resp = await self.safe_request(
            provider=provider,
            method="POST",
            path="/api/merchant/payments/status",
            body={"merchantId": str(merchant_id), "paymentId": external_order_id},
            timeout_ms=timeout_ms,
            log_event="poll_failed",
        )
        if resp is None or resp.status_code != 200:
            return None
        envelope = self.json_or_none(resp) or {}
        # Garex's status endpoint may return the order in different shapes
        # depending on whether one-id or many-ids was requested. Look both
        # in the envelope and in a nested ``result``.
        data = envelope.get("result") or envelope
        status = self.try_parse_provider_status(data.get("state"))
        if status is None:
            return None
        return ParsedCallback(
            external_order_id=str(data.get("id") or external_order_id),
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
            path="/api/merchant/balance",
            timeout_ms=timeout_ms,
            log_event="balance_failed",
        )
        if resp is None or resp.status_code != 200:
            return None
        envelope = self.json_or_none(resp) or {}
        return self.safe_decimal(envelope.get("amount"))

    async def get_upstream_rate(
        self,
        *,
        provider: CascadeProvider,
        timeout_ms: Optional[int] = None,
    ) -> Optional[Decimal]:
        """Fetch Garex's current quoted rate (USDT per fiat).

        Not part of the abstract contract — exposed for admin/debug
        tooling. Returns ``None`` on any failure.
        """
        resp = await self.safe_request(
            provider=provider,
            method="GET",
            path="/api/merchant/rate",
            timeout_ms=timeout_ms,
            log_event="rate_failed",
        )
        if resp is None or resp.status_code != 200:
            return None
        envelope = self.json_or_none(resp) or {}
        return self.safe_decimal(envelope.get("rate"))

    # ─── Provider-specific helpers ──────────────────────────────
    # _merchant_id / _method_value / _bank_code are inherited from base
    # as get_settings / resolve_method_value / resolve_bank_code.

    async def _set_status(
        self,
        *,
        provider: CascadeProvider,
        external_order_id: str,
        status: str,
        file_path: Optional[str],
        timeout_ms: Optional[int],
        log_event: str,
    ) -> bool:
        """PATCH /api/merchant/payments/<id>/status with optional base64 file.

        Garex unifies cancel / mark-paid / dispute behind the one status
        change endpoint; the only difference is the ``status`` value and
        whether a proof file is attached.
        """
        body: Dict[str, Any] = {"status": status}
        if file_path:
            data_url = self.file_to_data_url(file_path)
            if data_url:
                body["file"] = data_url
            else:
                logger.warning(
                    "garex_status_file_unreadable",
                    provider_id=provider.id,
                    external_order_id=external_order_id,
                    file_path=file_path,
                )

        resp = await self.safe_request(
            provider=provider,
            method="PATCH",
            path=f"/api/merchant/payments/{external_order_id}/status",
            body=body,
            timeout_ms=timeout_ms,
            log_event=log_event,
        )
        # 200 OK or 404 (already terminal) both mean "we did our best".
        return resp is not None and resp.status_code in (200, 204, 404)
