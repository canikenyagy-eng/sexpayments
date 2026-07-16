"""Safeway cascade adapter.

Provider docs: Safeway "Prime" H2H API.

Auth is the simplest scheme we support — a single ``Access-Token`` header
carrying the integration token verbatim (no bearer prefix, no timestamp, no
body signature), plus the mandatory ``Accept: application/json``.

We use the **H2H API** (``POST /api/h2h/order``) because, unlike the Merchant
API, it returns the actual payment requisite (``payment_detail``) the cascade
must hand to the payer. ``merchant_id`` is Safeway's merchant uuid — a
per-provider SETTING, not our internal id.

Receipts are forwarded **non-destructively**: ``notify_receipt`` posts the
base64 image to ``POST /api/h2h/order/{external_id}/receipt``, which keeps the
order live (the receipt endpoint is keyed by OUR ``external_id`` — resolved from
the order — not Safeway's ``order_id``). Escalation is a separate action:
``raise_dispute`` posts evidence to ``POST /api/h2h/order/{order_id}/dispute``
(which closes the open order and opens a dispute, per the docs).

Status callbacks are **unsigned** (the docs define no webhook signature), so
inbound verification is optional — see ``verify_and_decode_callback``.

Per-provider settings (``CascadeProvider.settings``):
  merchant_id*            — Safeway merchant uuid (required; their «Настройки»)
  default_currency        — currency sent when no bank is pinned (default rub)
  callback_url            — webhook url; default = our cascade callback endpoint
  expires_default_seconds — TTL fallback when the response carries no expires_at
  method_map              — our PaymentMethod → Safeway payment_detail_type
  bank_code_map           — our PaymentOption.code → Safeway payment_gateway code
  bank_response_aliases   — extra spellings for bank-name → our code resolution
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Mapping, Optional, Union

import httpx

from app.common.enums.cascading import ProviderStatus
from app.common.enums.payments import PaymentMethod
from app.core.logging import get_logger
from app.modules.cascading.integrations.base import (
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


class SafewayAdapter(ProviderAdapter):
    code = "safeway"
    display_name = "Safeway"
    description = (
        "P2P-площадка Safeway (H2H API). Авторизация — один заголовок "
        "Access-Token. Курс берётся из их ответа (conversion_price). Чеки "
        "пересылаются в POST .../{external_id}/receipt (заказ остаётся живым); "
        "диспут (POST .../dispute) — отдельная эскалация."
    )
    supports_provider_rate = True

    SUPPORTED_METHODS = (PaymentMethod.SBP, PaymentMethod.CARD)

    # ─── Signing knobs ───
    # One raw header, no timestamp / body signature.
    AUTH_HEADER = "Access-Token"
    AUTH_SCHEME = ""
    SIGN_REQUESTS = False
    TIMESTAMP_HEADER = ""
    SIGNATURE_HEADER = ""
    IDEMPOTENCY_HEADER = ""  # idempotency lives in our external_id

    # ─── Аутентификация: один токен ───
    CREDENTIALS_SCHEMA = (
        AdapterFieldSpec(
            key="api_secret",
            label="Access-Token",
            type="string",
            secret=True,
            required=True,
            description=(
                "Токен из админки Safeway (раздел «Интеграция»). Отправляется "
                "в заголовке Access-Token. Подписи у вебхуков нет — поле "
                "Webhook secret не требуется."
            ),
        ),
    )
    REQUIRED_CREDENTIALS = ("api_secret",)
    REQUIRED_SETTINGS = ("merchant_id",)

    # ─── Method mapping: our PaymentMethod → Safeway payment_detail_type ───
    DEFAULT_METHOD_MAP = {
        PaymentMethod.SBP.value: "phone",
        PaymentMethod.CARD.value: "card",
    }
    METHOD_MAP_SETTING_KEY = "method_map"
    PROVIDER_METHOD_TO_OURS = {
        "phone": PaymentMethod.SBP,
        "qr_code": PaymentMethod.SBP,
        "card": PaymentMethod.CARD,
        "account_number": PaymentMethod.CARD,
    }

    # ─── Bank mapping: our PaymentOption.code → Safeway payment_gateway code ───
    # RUB-only (per project scope). Unknown bank → None → we send currency=rub
    # so Safeway picks any gateway for the method. Overridable via bank_code_map.
    BANK_MAP_SETTING_KEY = "bank_code_map"
    DEFAULT_BANK_GATEWAY_MAP: Dict[str, str] = {
        "sber": "sberbank_rub",
        "alfa": "alfabank_rub",
        "raiffeisen": "raiffeisen_rub",
        "otp": "otp_rub",
        "psb": "psb_rub",
        "mtsbank": "mts_rub",
        "domrfbank": "domrf_rub",
        "rosbank": "rosbank_rub",
        "tbank": "tbank",
        "ozon": "ozon",
        "wbbank": "wbbank",
        "vtb": "Vtb",
        "akbars": "ak_bars_rub",
        "gazprom": "Gazprombank",
        "rshb": "Rosselhoz",
        "mkb": "mkb_rub",
        "sovkom": "Sovcombank",
        "uralsib": "Uralsib",
        "novikom": "Novikombank",
        "jandeks-bank": "Yandex_rub",
        "bspb": "Spbbank_rub",
    }

    # ─── Reverse mapping: Safeway's response bank label → our PaymentOption.code ───
    # Consulted by ``resolve_payment_option_from_bank_name`` (parse / callback)
    # BEFORE the shared ``DEFAULT_BANK_ALIASES_BY_CODE``. Carries Safeway's exact
    # ``payment_gateway_name`` spellings — including banks the shared map doesn't
    # cover yet (ДОМ.РФ, Росбанк, МКБ, Новикомбанк, Банк Санкт-Петербург) and
    # provider-specific formatting ("Т-Банк", "OZON Банк", "Ак Барс Банк"). All
    # purely additive (admin can extend further via ``bank_response_aliases``).
    BANK_ALIASES_BY_CODE: Dict[str, List[str]] = {
        "sber": ["Сбербанк"],
        "alfa": ["Альфа-Банк"],
        "raiffeisen": ["Райффайзенбанк"],
        "otp": ["ОТП"],
        "psb": ["ПСБ"],
        "mtsbank": ["МТС Банк"],
        "domrfbank": ["ДОМ.РФ", "Дом.РФ", "Дом РФ"],
        "rosbank": ["Росбанк"],
        "ozon": ["OZON Банк"],
        "tbank": ["Т-Банк"],
        "wbbank": ["Wildberries (Вайлдбериз Банк)", "Вайлдбериз Банк", "Вайлдбериз"],
        "vtb": ["ВТБ Банк"],
        "akbars": ["Ак Барс Банк"],
        "gazprom": ["Газпромбанк"],
        "rshb": ["Россельхозбанк"],
        "mkb": ["МКБ Московский Кредитный Банк", "МКБ", "Московский кредитный банк"],
        "sovkom": ["Совкомбанк"],
        "uralsib": ["Уралсиб"],
        "novikom": ["Новикомбанк"],
        "jandeks-bank": ["Яндекс банк"],
        "bspb": ["Банк Санкт-Петербург", "БСПБ"],
    }

    # ─── Status mapping ───
    # We read ONLY Safeway's coarse ``status`` field — the three values below.
    # The granular ``sub_status`` is intentionally NOT consulted.
    _COARSE_STATUS_MAP: Dict[str, ProviderStatus] = {
        "success": ProviderStatus.SUCCESS,
        "pending": ProviderStatus.PENDING,
        "fail": ProviderStatus.FAILED,
    }

    SETTINGS_SCHEMA = (
        AdapterFieldSpec(
            key="merchant_id",
            label="Merchant ID (uuid Safeway)",
            type="string",
            required=True,
            description=(
                "UUID мерчанта в Safeway (страница мерчанта → «Настройки»). "
                "Уходит в каждый запрос создания сделки."
            ),
        ),
        AdapterFieldSpec(
            key="default_currency",
            label="Валюта по умолчанию",
            type="string",
            default="rub",
            placeholder="rub",
            description="Шлётся как currency, когда банк не закреплён за заказом.",
        ),
        AdapterFieldSpec(
            key="callback_url",
            label="Callback URL",
            type="url",
            description=(
                "POST-ссылка для уведомлений о смене статуса. Пусто = наш "
                "cascade-вебхук https://<host>/api/cascade/v1/callbacks/safeway."
            ),
            placeholder="https://example.com/api/cascade/v1/callbacks/safeway",
        ),
        AdapterFieldSpec(
            key="expires_default_seconds",
            label="TTL реквизита (сек)",
            type="number",
            default=900,
            min=60,
            max=86400,
            description="Используется, если Safeway не вернул expires_at.",
        ),
        AdapterFieldSpec(
            key="method_map",
            label="Маппинг методов (наш → payment_detail_type)",
            type="kv_map",
            value_type="string",
            description=(
                "Ключи — наши коды (sbp/card), значения — Safeway "
                "payment_detail_type (phone/card/...). Пусто = sbp→phone, card→card."
            ),
        ),
        AdapterFieldSpec(
            key="bank_code_map",
            label="Маппинг банков (наш PaymentOption.code → Safeway payment_gateway)",
            type="kv_map",
            value_type="string",
            description=(
                "Ключ — наш код (sber), значение — код шлюза Safeway "
                "(sberbank_rub). Пусто = встроенный дефолт; неизвестный банк → "
                "шлём currency (любой шлюз метода)."
            ),
        ),
        AdapterFieldSpec(
            key="bank_response_aliases",
            label="Алиасы банков (наш код → написания)",
            type="kv_map",
            value_type="string",
            description=(
                "Доп. написания названия банка из ответа для сопоставления с "
                "нашим PaymentOption.code (через запятую)."
            ),
        ),
    )

    # ─── ProviderAdapter contract ──────────────────────────────
    # supports() / issue_requisite() inherited from base (Template Method).

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
        # Base emits the Access-Token header; Safeway also mandates Accept.
        headers = super().sign_request(
            token=token, method=method, path=path, body=body,
            idempotency_key=idempotency_key, extra_headers=extra_headers,
            provider=provider,
        )
        headers.setdefault("Accept", "application/json")
        return headers

    def resolve_bank_code(
        self, provider: CascadeProvider, payment_option_code: Optional[str]
    ) -> Optional[str]:
        """Our PaymentOption.code → Safeway payment_gateway code. Per-provider
        ``bank_code_map`` wins; else the built-in default; unknown → None (so
        the caller sends ``currency`` instead of ``payment_gateway``)."""
        if not payment_option_code:
            return None
        bank_map = self.get_settings(provider).get(self.BANK_MAP_SETTING_KEY) or {}
        mapped = bank_map.get(payment_option_code)
        if mapped:
            return mapped
        return self.DEFAULT_BANK_GATEWAY_MAP.get(payment_option_code)

    def build_payin_request(
        self,
        *,
        provider: CascadeProvider,
        order_data: Dict[str, Any],
        idempotency_key: str,
        method: PaymentMethod,
        method_value: str,
    ) -> Union[PayinRequest, ProviderRefusal]:
        settings = self.get_settings(provider)
        merchant_id = str(settings.get("merchant_id") or "")
        if not merchant_id:
            return ProviderRefusal(code="misconfigured", message="Safeway: merchant_id not set")

        # The provider-facing id MUST be OUR internal order id (order.uuid), never
        # the merchant's external_id / request id — we do not leak the merchant's
        # identifier to the provider. Safeway keys both idempotency (no header —
        # see IDEMPOTENCY_HEADER) and the receipt endpoint on this value, so
        # order.uuid (stable per order, globally unique) is exactly right. Falls
        # back to the idempotency_key on order-less paths (admin probe / CLI).
        external_id = str(order_data.get("uuid") or idempotency_key)
        callback_url = settings.get("callback_url") or self.build_cascade_callback_url(provider.code)

        body: Dict[str, Any] = {
            "external_id": external_id,
            "amount": int(Decimal(str(order_data["amount"]))),  # docs: целое число
            "merchant_id": merchant_id,
            "payment_detail_type": method_value,  # phone / card
        }
        if callback_url:
            body["callback_url"] = str(callback_url)

        # payment_gateway and currency are mutually exclusive: pin the bank when
        # one is resolved, else scope by currency (any gateway for the method).
        bank = self.resolve_bank_code(provider, order_data.get("payment_option_code"))
        if bank:
            body["payment_gateway"] = bank
        else:
            body["currency"] = str(settings.get("default_currency") or "rub")

        return PayinRequest(http_method="POST", path="/api/h2h/order", body=body)

    def parse_payin_response(
        self,
        *,
        provider: CascadeProvider,
        resp: httpx.Response,
        order_data: Dict[str, Any],
        fallback_method: PaymentMethod,
    ) -> IssueResult:
        data = self._unwrap(resp)
        if isinstance(data, ProviderRefusal):
            return data

        order_id = data.get("order_id")
        if not order_id:
            return ProviderRefusal(
                code="bad_response", message="Safeway: no order_id", raw=data,
            )

        detail = data.get("payment_detail") or {}
        account_number = str(detail.get("detail") or "")
        if not account_number:
            # No requisite issued (e.g. waiting_details_to_be_selected) — the
            # cascade needs one now, so move on to the next provider.
            return ProviderRefusal(
                code="no_capacity", message="Safeway: no requisite returned", raw=data,
            )

        bank_name = str(data.get("payment_gateway_name") or "")
        method_ours = self.resolve_payment_method_from(
            detail.get("detail_type"), default=fallback_method
        )
        amount_fiat = (
            self.safe_decimal(data.get("amount"))
            or self.safe_decimal(order_data.get("amount"))
            or Decimal("0")
        )

        return ProviderRequisiteResponse(
            external_order_id=str(order_id),
            bank_name=bank_name,
            account_number=account_number,
            account_holder=str(detail.get("initials") or ""),
            payment_method=method_ours,
            payment_option_code=self.resolve_payment_option_from_bank_name(
                bank_name, provider=provider
            ),
            amount_fiat=amount_fiat,
            expires_at=self._unix_to_dt(data.get("expires_at")) or self.default_expires_at(provider),
            raw=data,
            # conversion_price = RUB per USDT — same orientation as our rate.
            provider_rate=self.safe_decimal(data.get("conversion_price")),
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
            method="PATCH",
            path=f"/api/h2h/order/{external_order_id}/cancel",
            timeout_ms=timeout_ms,
            log_event="cancel_network_error",
        )
        # 200 = canceled; 400/404 = already closed / gone — treat as success.
        return resp is not None and resp.status_code in (200, 400, 404)

    async def notify_receipt(
        self,
        *,
        provider: CascadeProvider,
        external_order_id: str,
        receipt_path: str,
        comment: Optional[str],
    ) -> bool:
        """Forward the customer's receipt to Safeway WITHOUT touching the order's
        lifecycle: POST it to the dedicated receipt endpoint, which keeps the
        order live (unlike a dispute, which closes it). Escalation to a dispute
        is a separate action — see ``raise_dispute``.

        The receipt endpoint is keyed by OUR ``external_id`` (the id we sent at
        create), not Safeway's ``order_id``, so we resolve it from the order
        first. No receipt path = nothing to attach → no-op success (returning
        False would make the cascade worker retry up to 5× for a receipt that
        will never materialise — see ``forward_receipt_to_provider``)."""
        if not receipt_path:
            return True
        b64 = self._receipt_b64(receipt_path)
        if b64 is None:
            return False
        external_id = await self._resolve_external_id(provider, external_order_id)
        if not external_id:
            logger.warning(
                "safeway_receipt_no_external_id",
                provider_id=provider.id, external_order_id=external_order_id,
            )
            return False
        body: Dict[str, Any] = {"receipt": b64}
        name = self._receipt_filename(receipt_path)
        if name:
            body["receipt_name"] = name
        resp = await self.safe_request(
            provider=provider,
            method="POST",
            path=f"/api/h2h/order/{external_id}/receipt",
            body=body,
            timeout_ms=provider.request_timeout_ms,
            log_event="receipt_upload_failed",
        )
        return resp is not None and resp.status_code in (200, 201)

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
                "safeway_dispute_no_evidence",
                provider_id=provider.id, external_order_id=external_order_id,
            )
            return False
        ok = True
        for path in evidence_paths:
            if not await self._open_dispute(
                provider=provider, external_order_id=external_order_id,
                receipt_path=path, log_event="dispute_failed",
            ):
                ok = False
        return ok

    def parse_callback(
        self,
        *,
        provider: CascadeProvider,
        headers: Dict[str, str],
        body: bytes,
        query_params: Optional[Mapping[str, str]] = None,
    ) -> ParsedCallback:
        payload = self.verify_and_decode_callback(
            provider=provider, headers=headers, body=body
        )
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload

        external_id = data.get("order_id")
        if not external_id:
            raise CallbackVerificationError("Safeway callback missing order_id")

        status = self._map_status(data.get("status"))
        paid_amount: Optional[Decimal] = None
        if status in (ProviderStatus.SUCCESS, ProviderStatus.PAID):
            paid_amount = self.safe_decimal(data.get("amount"))

        return ParsedCallback(
            external_order_id=str(external_id),
            status=status,
            raw=payload,
            paid_amount_fiat=paid_amount,
        )

    def verify_and_decode_callback(
        self,
        *,
        provider: CascadeProvider,
        headers: Mapping[str, str],
        body: bytes,
    ) -> Dict[str, Any]:
        """Safeway webhooks are UNSIGNED (the docs define no signature). So
        inbound verification is optional: if a webhook secret AND a signature
        header are both present we still verify (defense in depth); otherwise we
        accept the unsigned body. Protect the endpoint with an IP allowlist if
        stronger guarantees are needed."""
        secret = self.get_webhook_signing_secret(provider)
        if secret and self.SIGNATURE_HEADER and self._lookup_header(headers, self.SIGNATURE_HEADER):
            self.verify_callback_signature(secret=secret, headers=headers, body=body)
        return self.decode_callback_body(body)

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
            path=f"/api/h2h/order/{external_order_id}",
            timeout_ms=timeout_ms,
            log_event="poll_failed",
        )
        if resp is None or resp.status_code != 200:
            return None
        env = self.json_or_none(resp) or {}
        data = env.get("data") if isinstance(env.get("data"), dict) else env
        if not isinstance(data, dict) or not data:
            return None
        try:
            status = self._map_status(data.get("status"))
        except CallbackVerificationError:
            return None
        return ParsedCallback(
            external_order_id=str(data.get("order_id") or external_order_id),
            status=status,
            raw={"event": "polled", "data": data},
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
            path="/api/wallet/balance",
            timeout_ms=timeout_ms,
            log_event="balance_failed",
        )
        if resp is None or resp.status_code != 200:
            return None
        env = self.json_or_none(resp) or {}
        data = env.get("data") if isinstance(env.get("data"), dict) else env
        # Safeway exposes the USDT balance directly.
        return self.safe_decimal((data or {}).get("balance"))

    # ─── helpers ───────────────────────────────────────────────

    def _unwrap(self, resp: httpx.Response) -> Union[Dict[str, Any], ProviderRefusal]:
        """Decode the ``{success, data}`` envelope → data dict, or a typed
        refusal for a non-2xx status / ``success:false`` body."""
        envelope = self.parse_envelope_or_refusal(
            resp, bad_response_message="Safeway: invalid JSON envelope"
        )
        if isinstance(envelope, ProviderRefusal):
            return envelope
        if not envelope.get("success"):
            return ProviderRefusal(
                code="no_capacity",
                message=str(envelope.get("message") or "Safeway declined"),
                raw=envelope,
            )
        data = envelope.get("data")
        return data if isinstance(data, dict) else {}

    def _map_status(self, status: Optional[str]) -> ProviderStatus:
        """Map Safeway's coarse ``status`` (success / pending / fail) to our
        ProviderStatus. The granular ``sub_status`` is intentionally ignored."""
        if status and status in self._COARSE_STATUS_MAP:
            return self._COARSE_STATUS_MAP[status]
        raise CallbackVerificationError(f"Unknown Safeway status ({status!r})")

    async def _open_dispute(
        self,
        *,
        provider: CascadeProvider,
        external_order_id: str,
        receipt_path: Optional[str],
        log_event: str,
    ) -> bool:
        """Attach a receipt by opening a Safeway dispute: POST the base64 image
        to ``/api/h2h/order/{id}/dispute``."""
        b64 = self._receipt_b64(receipt_path)
        if b64 is None:
            return False
        resp = await self.safe_request(
            provider=provider,
            method="POST",
            path=f"/api/h2h/order/{external_order_id}/dispute",
            body={"receipt": b64},
            timeout_ms=provider.request_timeout_ms,
            log_event=log_event,
        )
        return resp is not None and resp.status_code in (200, 201)

    def _receipt_b64(self, receipt_path: Optional[str]) -> Optional[str]:
        """Read a receipt file (within the upload dir) → raw base64 string
        (Safeway wants the base64 image, not a data URL). None on missing file."""
        if not receipt_path:
            return None
        data_url = self.file_to_data_url(receipt_path)
        if not data_url:
            return None
        # Strip the "data:<mime>;base64," prefix → raw base64 payload.
        return data_url.split(",", 1)[1] if "," in data_url else data_url

    @staticmethod
    def _receipt_filename(receipt_path: Optional[str]) -> Optional[str]:
        """Original filename for the optional ``receipt_name`` field — lets
        Safeway keep the extension when the file is sent as base64 (esp. PDFs)."""
        if not receipt_path:
            return None
        return os.path.basename(receipt_path) or None

    async def _resolve_external_id(
        self, provider: CascadeProvider, external_order_id: str
    ) -> Optional[str]:
        """Our ``external_id`` (sent at create) for a Safeway ``order_id`` — the
        receipt endpoint is keyed by external_id, not order_id, so read it back
        from ``GET /api/h2h/order/{order_id}``. None if the order can't be
        fetched or carries no external_id."""
        resp = await self.safe_request(
            provider=provider,
            method="GET",
            path=f"/api/h2h/order/{external_order_id}",
            timeout_ms=provider.request_timeout_ms,
            log_event="receipt_resolve_external_id_failed",
        )
        if resp is None or resp.status_code != 200:
            return None
        env = self.json_or_none(resp) or {}
        data = env.get("data") if isinstance(env.get("data"), dict) else env
        external_id = (data or {}).get("external_id")
        return str(external_id) if external_id else None

    @staticmethod
    def _unix_to_dt(value: Any) -> Optional[datetime]:
        """Safeway ``expires_at`` is a unix timestamp (seconds)."""
        if value is None:
            return None
        try:
            return datetime.fromtimestamp(int(value), tz=timezone.utc)
        except (TypeError, ValueError, OSError, OverflowError):
            return None
