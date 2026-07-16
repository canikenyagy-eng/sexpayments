"""GoSwifty cascade adapter.

Docs: https://goswifty.org/

Auth scheme differs from LegacyCrypto:
  * No bearer / no timestamp / no body-signed request signature.
  * Every authenticated call carries a single ``X-Secret`` header — the
    merchant's secret key from the Swifty cabinet.

Webhook signature is non-standard:
  ``X-Hash = sha256("{id}:{amount}:{status}:" + sha256(secret))``
  (nested sha256 — *not* an HMAC over the raw body).

merchantId is part of every URL and request body. We store it in
``CascadeProvider.settings.merchant_id``.

Status mapping (Swifty → our ProviderStatus):
  PENDING    → PENDING
  ACCEPTED   → SUCCESS
  CANCELLED  → CANCELED
  DISPUTE    → DISPUTED
  RESEND     → SUCCESS  (accepted after dispute)
  NOT_FOUND  → FAILED
  ERROR      → FAILED

Provider quotes its own rate (``course`` field) on every issue/check response,
so this adapter defaults to ``rate_source=provider``.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
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


class SwiftyAdapter(ProviderAdapter):
    code = "swifty"
    display_name = "GoSwifty"
    description = (
        "P2P-площадка api.goswifty.org. Поддерживает Card, SBP (phone/QR), SIM. "
        "Авторизация — только X-Secret. Курс берётся из их ответа (course)."
    )
    supports_provider_rate = True
    logo_filename = "swifty.svg"  # frontend-vue/public/logos/swifty.svg

    SUPPORTED_METHODS = (
        PaymentMethod.SBP,
        PaymentMethod.CARD,
        PaymentMethod.SIM,
    )

    # ─── Signing knobs ───
    # Swifty signs neither outbound bodies nor timestamps — just X-Secret.
    AUTH_HEADER = "X-Secret"
    AUTH_SCHEME = ""  # bare token, no "Bearer "
    SIGN_REQUESTS = False  # no HMAC payload signing on outbound
    IDEMPOTENCY_HEADER = ""  # Swifty's idempotency lives on orderId in the body
    # SIGNATURE_HEADER is repurposed for the webhook (X-Hash) — see
    # verify_callback_signature below. We never produce X-Signature for outbound.
    SIGNATURE_HEADER = "X-Hash"

    # ─── Аутентификация ───
    CREDENTIALS_SCHEMA = (
        AdapterFieldSpec(
            key="api_secret",
            label="X-Secret (merchant secret)",
            type="string",
            secret=True,
            required=True,
            description=(
                "Секретный ключ мерчанта из кабинета GoSwifty. Шлётся "
                "в заголовке X-Secret на каждом запросе."
            ),
        ),
        AdapterFieldSpec(
            key="webhook_secret",
            label="Webhook secret (для X-Hash)",
            type="string",
            secret=True,
            description=(
                "Секрет для верификации X-Hash на webhook'ах. "
                "Алгоритм: sha256(id:amount:status:sha256(secret))."
            ),
        ),
    )

    # ─── Declarative method mapping ───
    DEFAULT_METHOD_MAP = {
        PaymentMethod.SBP.value: "phone",
        PaymentMethod.CARD.value: "card",
        PaymentMethod.SIM.value: "sim",
    }
    METHOD_MAP_SETTING_KEY = "method_code_map"
    # subcode → our method (used while parsing the response). Adapters can
    # still override per-provider via settings.subcode_method_map.
    PROVIDER_METHOD_TO_OURS = {
        "card": PaymentMethod.CARD,
        "phone": PaymentMethod.SBP,
        "qr": PaymentMethod.SBP,
        "sim": PaymentMethod.SIM,
        "account": PaymentMethod.CARD,
    }
    REQUIRED_SETTINGS = ("merchant_id",)
    REQUIRED_CREDENTIALS = ("api_secret",)

    PROVIDER_STATUS_MAP = {
        "PENDING": ProviderStatus.PENDING,
        "ACCEPTED": ProviderStatus.SUCCESS,
        "CANCELLED": ProviderStatus.CANCELED,
        "DISPUTE": ProviderStatus.DISPUTED,
        "RESEND": ProviderStatus.SUCCESS,
        "NOT_FOUND": ProviderStatus.FAILED,
        "ERROR": ProviderStatus.FAILED,
    }

    SETTINGS_SCHEMA = (
        AdapterFieldSpec(
            key="merchant_id",
            label="Merchant ID",
            type="number",
            required=True,
            description=(
                "Числовой ID мерчанта из личного кабинета Swifty. Идёт в URL "
                "balance/check/payout и в body запросов /payment, /decline."
            ),
        ),
        AdapterFieldSpec(
            key="default_subcode",
            label="Default subcode (тип реквизита)",
            type="select",
            default="card",
            options=[
                AdapterFieldOption("card", "Карта"),
                AdapterFieldOption("phone", "Телефон"),
                AdapterFieldOption("qr", "QR"),
                AdapterFieldOption("sim", "SIM"),
                AdapterFieldOption("account", "Account"),
            ],
            description=(
                "Какой тип реквизита просить у Swifty, если наш payment_method "
                "это CARD. Для SBP всегда отправляем phone, для SIM — sim."
            ),
        ),
        AdapterFieldSpec(
            key="method_code_map",
            label="Маппинг методов (наш → Swifty code)",
            type="kv_map",
            value_type="select",
            options=[
                AdapterFieldOption("card", "card"),
                AdapterFieldOption("phone", "phone"),
                AdapterFieldOption("qr", "qr"),
                AdapterFieldOption("sim", "sim"),
                AdapterFieldOption("account", "account"),
            ],
            description=(
                "Whitelist + override дефолтного маппинга. Ключи — наши коды "
                "(sbp/card/sim), значения — code для Swifty. Пусто = sbp→phone, "
                "card→card, sim→sim."
            ),
        ),
        AdapterFieldSpec(
            key="subcode_method_map",
            label="Маппинг subcode (Swifty → наш PaymentMethod)",
            type="kv_map",
            value_type="select",
            options=[
                AdapterFieldOption("sbp", "СБП"),
                AdapterFieldOption("card", "Карта"),
                AdapterFieldOption("sim", "SIM"),
            ],
            description=(
                "Как считать наш payment_method, когда Swifty в ответе шлёт "
                "method.subcode. Пусто = card→card, phone→sbp, qr→sbp, sim→sim, "
                "account→card."
            ),
        ),
    )

    # ─── Custom webhook verification: X-Hash = sha256(id:amount:status:sha256(secret)) ───

    def verify_callback_signature(
        self,
        *,
        secret: str,
        headers: Mapping[str, str],
        body: bytes,
    ) -> None:
        received = self._lookup_header(headers, self.SIGNATURE_HEADER)
        if not received:
            raise CallbackVerificationError(
                f"Missing {self.SIGNATURE_HEADER} header"
            )

        # We need id/amount/status from the body itself — base implementation
        # signs raw body, here we sign a concatenation of selected fields.
        try:
            payload = json.loads(body.decode("utf-8"), parse_float=Decimal)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CallbackVerificationError(f"Bad JSON body: {exc}")
        if not isinstance(payload, dict):
            raise CallbackVerificationError("Callback body must be a JSON object")
        try:
            order_id = payload["id"]
            amount = payload["amount"]
            status = payload["status"]
        except KeyError as exc:
            raise CallbackVerificationError(
                f"Webhook body missing required field for X-Hash: {exc}"
            )

        amount_str = _format_amount_for_hash(amount)
        inner = hashlib.sha256(secret.encode("utf-8")).hexdigest()
        expected_input = f"{order_id}:{amount_str}:{status}:{inner}"
        expected = hashlib.sha256(expected_input.encode("utf-8")).hexdigest()

        if not hmac.compare_digest(expected, received):
            raise CallbackVerificationError("Invalid X-Hash signature")

    # ─── ProviderAdapter contract ────────────────────────────
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
        body: Dict[str, Any] = {
            "merchantId": self._merchant_id(provider),
            "orderId": order_data.get("merchant_request_id") or idempotency_key,
            "amount": float(Decimal(str(order_data["amount"]))),
            "code": method_value,
        }
        subcode = self._subcode_for(provider, method)
        if subcode:
            body["subcode"] = subcode
        return PayinRequest(
            http_method="POST",
            path="/v1/public/payment",
            body=body,
        )

    async def cancel_request(
        self,
        *,
        provider: CascadeProvider,
        external_order_id: str,
        timeout_ms: int,
    ) -> bool:
        merchant_id = self._merchant_id(provider)
        if merchant_id is None:
            return False
        resp = await self.safe_request(
            provider=provider,
            method="POST",
            path="/v1/public/payment/decline",
            body={"merchantId": merchant_id, "tradeId": external_order_id},
            timeout_ms=timeout_ms,
            log_event="cancel_network_error",
        )
        # 200 means the trade transitioned to CANCELLED; 404 means it's already gone.
        return resp is not None and resp.status_code in (200, 404)

    async def notify_receipt(
        self,
        *,
        provider: CascadeProvider,
        external_order_id: str,
        receipt_path: str,
        comment: Optional[str],
    ) -> bool:
        """Swifty has no separate "merchant uploaded receipt" hook — they treat
        the receipt as a dispute attachment. We forward it via /dispute and
        return True only on 200 PENDING.
        """
        return await self._submit_dispute(
            provider=provider,
            external_order_id=external_order_id,
            evidence_paths=[receipt_path] if receipt_path else [],
        )

    async def raise_dispute(
        self,
        *,
        provider: CascadeProvider,
        external_order_id: str,
        reason: str,
        evidence_paths: List[str],
    ) -> bool:
        ok = True
        if not evidence_paths:
            # POST /dispute without a file is still accepted; it transitions
            # the trade into DISPUTE state.
            ok &= await self._submit_dispute(
                provider=provider,
                external_order_id=external_order_id,
                evidence_paths=[],
            )
            return ok
        for path in evidence_paths:
            if not await self._submit_dispute(
                provider=provider,
                external_order_id=external_order_id,
                evidence_paths=[path],
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
        # Webhook signature uses X-Hash + the nested sha256 scheme — handled by
        # our overridden verify_callback_signature. If no webhook secret is
        # configured (dev mode), the base helper skips the check.
        payload = self.verify_and_decode_callback(
            provider=provider, headers=headers, body=body
        )
        external_id = payload.get("id")
        if not external_id:
            raise CallbackVerificationError("Webhook missing id")

        status = self.parse_provider_status(payload.get("status"))

        paid_amount: Optional[Decimal] = None
        if status in (ProviderStatus.SUCCESS, ProviderStatus.PAID):
            paid_amount = self.safe_decimal(payload.get("amount"))

        # Their callback also carries rate/feePercent — capture provider_rate
        # in raw for downstream consumers.
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
        merchant_id = self._merchant_id(provider)
        if merchant_id is None:
            return None
        resp = await self.safe_request(
            provider=provider,
            method="GET",
            path=f"/v1/public/payment/{merchant_id}/check/{external_order_id}",
            timeout_ms=timeout_ms,
            log_event="poll_failed",
        )
        if resp is None or resp.status_code != 200:
            return None
        envelope = self.json_or_none(resp) or {}
        status = self.try_parse_provider_status(envelope.get("status"))
        if status is None:
            return None
        data = envelope.get("data") or {}
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
        merchant_id = self._merchant_id(provider)
        if merchant_id is None:
            return None
        resp = await self.safe_request(
            provider=provider,
            method="GET",
            path=f"/v1/public/merchant/{merchant_id}/balance",
            timeout_ms=timeout_ms,
            log_event="balance_failed",
        )
        if resp is None or resp.status_code != 200:
            return None
        envelope = self.json_or_none(resp) or {}
        data = envelope.get("data") or {}
        return self.safe_decimal(data.get("balance"))

    # ─── Provider-specific helpers ─────────────────────────────

    def _merchant_id(self, provider: CascadeProvider) -> Optional[int]:
        raw = self.get_settings(provider).get("merchant_id")
        if raw is None:
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    def _subcode_for(
        self, provider: CascadeProvider, method: PaymentMethod
    ) -> Optional[str]:
        """What to send as ``subcode`` for a given internal method.

        Swifty's convention: when code == card/phone/qr/account/sim the
        subcode is optional. We only send subcode when the admin explicitly
        chose a non-default. Empty → omit (provider defaults to "card").
        """
        cfg = self.get_settings(provider)
        if method == PaymentMethod.CARD:
            return cfg.get("default_subcode") or None
        # SBP-style requests don't need a subcode; SIM is its own code.
        return None

    def _subcode_to_method(
        self, provider: CascadeProvider, subcode: Optional[str]
    ) -> Optional[PaymentMethod]:
        """Swifty subcode → our PaymentMethod. Honours per-provider
        ``subcode_method_map`` override first; falls back to the
        declarative ``PROVIDER_METHOD_TO_OURS`` class-attr.
        """
        if not subcode:
            return None
        overrides = self.get_settings(provider).get("subcode_method_map") or {}
        mapped = overrides.get(subcode)
        if mapped:
            try:
                return PaymentMethod(mapped)
            except ValueError:
                return None
        return self.PROVIDER_METHOD_TO_OURS.get(subcode)

    def parse_payin_response(
        self,
        *,
        provider: CascadeProvider,
        resp: httpx.Response,
        order_data: Dict[str, Any],
        fallback_method: PaymentMethod,
    ) -> IssueResult:
        try:
            envelope = resp.json() or {}
        except (ValueError, AttributeError):
            envelope = {}
        status_raw = envelope.get("status")
        data = envelope.get("data") or {}

        # Successful issue = PENDING + payment details. Anything else is a
        # refusal (NOT_FOUND, ERROR) or a transport-level failure (handled by
        # refusal_from_response for non-2xx).
        if resp.status_code != 200 and resp.status_code != 201:
            if status_raw == "NOT_FOUND":
                return ProviderRefusal(
                    code="no_capacity",
                    message=data.get("message") or "Swifty: requisite not found",
                    raw=envelope,
                )
            return self.refusal_from_response(resp)

        if status_raw != "PENDING":
            # 200 OK with status=ERROR/NOT_FOUND happens too — treat the same.
            code = (
                "no_capacity"
                if status_raw == "NOT_FOUND"
                else f"status_{(status_raw or 'unknown').lower()}"
            )
            return ProviderRefusal(
                code=code,
                message=data.get("message") or "Swifty: unexpected status",
                raw=envelope,
            )

        method_info = data.get("method") or {}
        method_ours = (
            self._subcode_to_method(provider, method_info.get("subcode"))
            or fallback_method
        )

        course = data.get("course")
        provider_rate = Decimal(str(course)) if course is not None else None

        amount_str = data.get("amount")
        amount_fiat = (
            Decimal(str(amount_str))
            if amount_str is not None
            else Decimal(str(order_data.get("amount", "0")))
        )

        expire_unix = data.get("expire")
        expires_at: Optional[datetime] = None
        if expire_unix is not None:
            try:
                expires_at = datetime.fromtimestamp(int(expire_unix), tz=timezone.utc)
            except (TypeError, ValueError, OSError):
                expires_at = None
        if expires_at is None:
            expires_at = self.default_expires_at(provider)

        bank_name = str(method_info.get("methodName") or method_info.get("code") or "")
        
        option_code = (
            self.resolve_payment_option_from_bank_name(bank_name, provider=provider)
            or method_info.get("code")
        )

        return ProviderRequisiteResponse(
            external_order_id=str(data.get("id") or ""),
            bank_name=bank_name,
            account_number=str(method_info.get("number") or ""),
            # Holder comes from the provider's `comment`; blank when absent.
            # Never fall back to the provider name — it must not leak the
            # provider / that this is a cascade order to the merchant.
            account_holder=str(method_info.get("comment") or ""),
            payment_method=method_ours,
            payment_option_code=option_code,
            amount_fiat=amount_fiat,
            expires_at=expires_at,
            raw=envelope,
            provider_rate=provider_rate,
        )

    async def _submit_dispute(
        self,
        *,
        provider: CascadeProvider,
        external_order_id: str,
        evidence_paths: List[str],
    ) -> bool:
        merchant_id = self._merchant_id(provider)
        if merchant_id is None:
            return False

        form_data = {
            "merchantId": str(merchant_id),
            "tradeId": external_order_id,
        }
        # Swifty accepts /dispute without a file (opens an empty dispute) — pass
        # file_path=None in that case and upload_file skips the multipart bit.
        resp = await self.upload_file(
            provider=provider,
            method="POST",
            path="/v1/public/dispute",
            file_path=evidence_paths[0] if evidence_paths else None,
            file_field="file",
            form_data=form_data,
            timeout_ms=provider.request_timeout_ms,
            log_event="dispute_failed",
        )
        if resp is None or resp.status_code != 200:
            return False
        envelope = self.json_or_none(resp) or {}
        return envelope.get("status") == "PENDING"


def _format_amount_for_hash(amount: Any) -> str:
    """Render amount for X-Hash exactly the way Swifty signs it on their side.

    Swifty signs the JSON literal byte-for-byte: ``5034.05`` → ``"5034.05"``,
    ``1000.00`` → ``"1000.00"`` (trailing zeros kept). The verifier upstream
    parses JSON numbers with ``parse_float=Decimal`` so this contract holds
    via ``str(Decimal('1000.00')) == '1000.00'``.

    Float inputs are a fallback for callers that pre-parsed without Decimal —
    we use ``format(x, 'g')`` to drop float-repr artefacts, but trailing
    zeros are lost (this is the case that broke production until we switched
    the verifier to Decimal parsing). Integers stringify directly.
    """
    if isinstance(amount, Decimal):
        return str(amount)
    if isinstance(amount, float):
        return format(amount, "g")
    return str(amount)
