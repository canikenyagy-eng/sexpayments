"""Bitwire cascade adapter.

Docs: https://api.bitwire.finance/

Bitwire is the first provider in our integration zoo with three patterns
we hadn't seen before, all of which are now first-class in the base:

  1. **JWT-with-refresh auth**: a separate ``POST /api/auth/sign-in``
     issues a JWT with ``dateTimeExpires``. Every authed call ships the
     JWT in ``Authorization: Bearer`` and refreshes it via re-sign-in
     when it expires. We implement the cache + refresh in
     ``acquire_token`` (new async hook on the base).

  2. **Dual headers in parallel**: every request carries the JWT AND a
     static ``X-Api-Key`` — this is BridgePay-shaped dual-credential
     but without HMAC signing. The base's ``EXTRA_AUTH_HEADER`` knob
     handles it declaratively (no override).

  3. **GET callback with query string**: status updates arrive as
     ``GET {callbackUrl}?id=...&status=...&reconciliationSum=...`` with
     **no body**. ``parse_callback`` receives them via the new
     ``query_params`` keyword argument.

Endpoints we use:
  * ``POST /api/auth/sign-in`` — JWT issuance (managed by acquire_token)
  * ``POST /api/merchant/order/<accountId>/deposit`` — pay-in request
  * ``POST /api/merchant/order/<dealId>/cancel`` — cancel pending order
  * ``GET  /api/merchant/balance`` — current store balance

There is no receipt-forward endpoint and no merchant-initiated dispute
endpoint — disputes flow from Bitwire to us (DISPUTE status arrives on
the callback). Both ``notify_receipt`` and ``raise_dispute`` are no-ops.

Status mapping (Bitwire → ProviderStatus):
  PENDING    → PENDING
  COMPLETED  → SUCCESS
  CANCELED   → CANCELED
  DISPUTE    → DISPUTED

Credentials storage in ``CascadeProvider``:
  api_key_encrypted     → X-Api-Key value (the static API key per store)
  api_secret_encrypted  → sign-in password
  settings.email        → sign-in email
  settings.account_id   → UUID for the {accountId} path slot
  settings.totp_code    → optional 2FA code; only set when the account
                          actually has 2FA enabled (re-roll regularly)

Per-provider settings (``CascadeProvider.settings``):
  email                   — required, used to mint JWT
  account_id              — required, UUID merchant account
  totp_code               — optional 2FA code
  default_currency        — fiat currency (RUB by default)
  callback_url            — URL Bitwire calls on status changes (GET)
  bank_code_map           — our PaymentOption.code → Bitwire ``issuer``
                            value. Empty = lowercase passthrough.
  expires_default_seconds — TTL fallback when timeExpires missing.
  jwt_refresh_skew_seconds — refresh JWT this many seconds before its
                             stated expiry (default 60).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Mapping, Optional, Tuple

import httpx

from app.common.enums.cascading import ProviderStatus
from app.common.enums.payments import PaymentMethod
from app.common.types import utcnow
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


class BitwireAdapter(ProviderAdapter):
    code = "bitwire"
    display_name = "Bitwire"
    description = (
        "P2P-площадка api.bitwire.finance. Поддерживает Card и SBP, "
        "трансграничные переводы. Аутентификация — JWT (логин по "
        "email/password) + статический X-Api-Key. Курс берётся из их "
        "ответа (currencyRate)."
    )
    supports_provider_rate = True

    SUPPORTED_METHODS = (PaymentMethod.SBP, PaymentMethod.CARD)

    # ─── Signing knobs ───
    # Primary creds: Authorization: Bearer <JWT> (JWT is dynamic — see
    # acquire_token below; ``token`` arg into sign_request is the JWT).
    AUTH_HEADER = "Authorization"
    AUTH_SCHEME = "Bearer"
    # Secondary creds: X-Api-Key — value auto-pulled from api_key_encrypted.
    EXTRA_AUTH_HEADER = "X-Api-Key"
    SIGN_REQUESTS = False  # no HMAC over body — Bearer alone is enough
    IDEMPOTENCY_HEADER = ""  # idempotency lives on internalId in the body

    # Bitwire does not expose a documented webhook signature scheme; the
    # X-Secret-Phrase we ship at create-time is for *outbound encryption*
    # of the callback body (not our concern). Skip verification.
    WEBHOOK_SECRET_SOURCE = "webhook_secret"  # falls back to None when unset

    # ─── Аутентификация: X-Api-Key + password (для sign-in/JWT) ───
    # ВАЖНО: email и account_id вынесены в SETTINGS_SCHEMA, а не сюда —
    # они не секреты, а конфиг. Сюда попадает только то, что хранится
    # в зашифрованных колонках провайдера.
    CREDENTIALS_SCHEMA = (
        AdapterFieldSpec(
            key="api_key",
            label="X-Api-Key (статический ключ магазина)",
            type="string",
            secret=True,
            required=True,
            description=(
                "Постоянный API-ключ магазина из кабинета Bitwire. "
                "Шлётся в X-Api-Key на каждом запросе параллельно "
                "с Bearer JWT."
            ),
        ),
        AdapterFieldSpec(
            key="api_secret",
            label="Пароль для sign-in",
            type="string",
            secret=True,
            required=True,
            description=(
                "Пароль учётной записи (email указывается в Настройках). "
                "Используется адаптером один раз при логине через "
                "POST /api/auth/sign-in. Полученный JWT кэшируется и "
                "обновляется автоматически при истечении."
            ),
        ),
    )

    # ─── Declarative method/bank mappings ───
    # Bitwire's method is a boolean isSbp, not a string — we keep
    # DEFAULT_METHOD_MAP populated with sentinels so the base ``supports``
    # and per-provider whitelist still works, and convert in
    # build_payin_request.
    DEFAULT_METHOD_MAP = {
        PaymentMethod.SBP.value: "sbp",
        PaymentMethod.CARD.value: "card",
    }
    METHOD_MAP_SETTING_KEY = "method_map"
    PROVIDER_METHOD_TO_OURS = {
        "sbp": PaymentMethod.SBP,
        "card": PaymentMethod.CARD,
    }
    BANK_CODE_TRANSFORM = "lower"  # Bitwire issuer codes are lowercase
    REQUIRED_SETTINGS = ("email", "account_id")
    REQUIRED_CREDENTIALS = ("api_key", "api_secret")

    PROVIDER_STATUS_MAP = {
        "PENDING": ProviderStatus.PENDING,
        "COMPLETED": ProviderStatus.SUCCESS,
        "CANCELED": ProviderStatus.CANCELED,
        "DISPUTE": ProviderStatus.DISPUTED,
    }

    SETTINGS_SCHEMA = (
        AdapterFieldSpec(
            key="email",
            label="Email для sign-in",
            type="string",
            required=True,
            placeholder="merchant@example.com",
            description=(
                "Email учётной записи на Bitwire. Используется для логина "
                "и получения JWT. JWT кэшируется и обновляется по истечении."
            ),
        ),
        AdapterFieldSpec(
            key="account_id",
            label="Account ID (UUID)",
            type="string",
            required=True,
            placeholder="550e8400-e29b-41d4-a716-446655440000",
            description=(
                "Идентификатор аккаунта мерчанта в Bitwire — UUID из "
                "ссылок их API (/order/{accountId}/deposit)."
            ),
        ),
        AdapterFieldSpec(
            key="totp_code",
            label="2FA код (TOTP)",
            type="string",
            secret=True,
            description=(
                "Только если у учётной записи включена двухфакторная "
                "аутентификация. Поскольку TOTP-код одноразовый, эту "
                "интеграцию рекомендуется использовать без 2FA, либо "
                "обновлять код перед каждым sign-in."
            ),
        ),
        AdapterFieldSpec(
            key="callback_url",
            label="Callback URL (GET)",
            type="url",
            description=(
                "URL, на который Bitwire отправит GET с query string при "
                "смене статуса. Должен совпадать с нашим "
                "/api/cascade/v1/callbacks/bitwire."
            ),
            placeholder="https://example.com/api/cascade/v1/callbacks/bitwire",
        ),
        AdapterFieldSpec(
            key="default_currency",
            label="Фиатная валюта по умолчанию",
            type="string",
            default="RUB",
            placeholder="RUB",
        ),
        AdapterFieldSpec(
            key="bank_code_map",
            label="Маппинг банков (наш PaymentOption.code → Bitwire issuer)",
            type="kv_map",
            value_type="string",
            description=(
                "Ключ — наш PaymentOption.code (sber, tinkoff), значение — "
                "Bitwire issuer (sberbank, tbank). Пусто = lowercase нашего кода."
            ),
        ),
        AdapterFieldSpec(
            key="expires_default_seconds",
            label="TTL реквизита (сек) — fallback",
            type="number",
            default=1800,
            min=60,
            max=86400,
            description="Используется, только если timeExpires не в ответе.",
        ),
        AdapterFieldSpec(
            key="jwt_refresh_skew_seconds",
            label="JWT refresh skew (сек)",
            type="number",
            default=60,
            min=0,
            max=600,
            description=(
                "За сколько секунд до истечения JWT'а обновлять токен. "
                "Дефолт 60: переподпись за минуту до expiry."
            ),
        ),
    )

    def __init__(self) -> None:
        # Per-instance JWT cache: {provider_id: (token, expires_at_utc)}.
        # The registry instantiates one BitwireAdapter shared across all
        # uses, so all callers benefit from cache hits. Tests instantiate
        # fresh adapters → clean cache slate.
        self._jwt_cache: Dict[int, Tuple[str, datetime]] = {}
        # Per-provider locks to avoid concurrent sign-in storms when many
        # in-flight requests notice the same expired token.
        self._jwt_locks: Dict[int, asyncio.Lock] = {}

    # ─── JWT acquisition (overrides base async hook) ───

    async def acquire_token(self, provider: CascadeProvider) -> str:
        """Return a cached-or-freshly-minted JWT for this provider.

        Re-signs when the cached token is missing, expired, or within
        ``jwt_refresh_skew_seconds`` of expiring. Concurrent callers
        contend on a per-provider asyncio Lock so only one sign-in races.
        """
        lock = self._jwt_locks.setdefault(provider.id, asyncio.Lock())
        async with lock:
            cached = self._jwt_cache.get(provider.id)
            skew = int(
                self.get_settings(provider).get("jwt_refresh_skew_seconds", 60)
            )
            if cached is not None:
                token, expires_at = cached
                if utcnow() + timedelta(seconds=skew) < expires_at:
                    return token
            # Stale or absent → sign in again.
            token, expires_at = await self._sign_in(provider)
            self._jwt_cache[provider.id] = (token, expires_at)
            return token

    async def _sign_in(self, provider: CascadeProvider) -> Tuple[str, datetime]:
        """POST /api/auth/sign-in and return ``(jwt, expires_at_utc)``."""
        settings = self.get_settings(provider)
        email = settings.get("email")
        password = self.get_token(provider)  # decrypts api_secret_encrypted
        if not email or not password:
            raise CallbackVerificationError(
                "Bitwire missing email/password for sign-in"
            )
        body: Dict[str, Any] = {"email": email, "password": password}
        totp = settings.get("totp_code")
        if totp:
            body["code"] = str(totp)

        # We can't reuse request_signed here — it would call acquire_token
        # recursively. Use a plain httpx client for the auth call. Route
        # through ``client.request`` so the test FakeClient (which only
        # implements .request) sees the same call shape as production.
        timeout_s = (provider.request_timeout_ms or 5000) / 1000
        async with httpx.AsyncClient(
            base_url=provider.base_url, timeout=timeout_s
        ) as client:
            resp = await client.request(
                "POST",
                "/api/auth/sign-in",
                json=body,
                headers={"Content-Type": "application/json"},
            )
        if resp.status_code != 200:
            raise CallbackVerificationError(
                f"Bitwire sign-in failed: {resp.status_code} {resp.text[:200]}"
            )
        payload = self.json_or_none(resp) or {}
        token = payload.get("token")
        expires_raw = payload.get("dateTimeExpires")
        if not token:
            raise CallbackVerificationError(
                "Bitwire sign-in response missing token"
            )
        expires_at = self.parse_iso8601(expires_raw)
        if expires_at is None:
            # Defensive fallback: assume 1 hour validity.
            expires_at = utcnow() + timedelta(hours=1)
        logger.info(
            "bitwire_jwt_refreshed",
            provider_id=provider.id,
            expires_at=expires_at.isoformat(),
        )
        return token, expires_at

    def invalidate_jwt(self, provider: CascadeProvider) -> None:
        """Force a re-sign-in on the next request — used in tests, and
        could be wired to a 401 retry path later if Bitwire revokes
        tokens out of band.
        """
        self._jwt_cache.pop(provider.id, None)

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
        account_id = str(settings["account_id"])
        callback_url = (
            order_data.get("bitwire_callback_url")
            or settings.get("callback_url")
            or self.build_cascade_callback_url(provider.code)
        )
        body: Dict[str, Any] = {
            "isSbp": method == PaymentMethod.SBP,
            "amount": self.format_amount(order_data["amount"]),
            "currency": settings.get("default_currency", "RUB"),
            "callbackUrl": str(callback_url) if callback_url else "",
            "internalId": (
                order_data.get("merchant_request_id") or idempotency_key
            ),
        }
        issuer = self.resolve_bank_code(provider, order_data.get("payment_option_code"))
        if issuer:
            body["issuer"] = issuer
        nspk = order_data.get("nspk_code")
        if nspk:
            body["nspkCode"] = str(nspk)
        user_id = order_data.get("client_user_id")
        if user_id:
            body["userId"] = str(user_id)
        # Optional cross-border currency — let callers pass it through.
        cross = order_data.get("cross_border_currency")
        if cross:
            body["crossBorderCurrency"] = str(cross)

        return PayinRequest(
            http_method="POST",
            path=f"/api/merchant/order/{account_id}/deposit",
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
        if resp.status_code == 404:
            return ProviderRefusal(
                code="no_capacity",
                message="Bitwire: no requisites available",
                raw={"status": 404, "body": resp.text[:200]},
            )

        envelope = self.parse_envelope_or_refusal(
            resp,
            ok_statuses=(200, 201),
            bad_response_message="Bitwire deposit: invalid JSON",
        )
        if isinstance(envelope, ProviderRefusal):
            return envelope

        order_id = envelope.get("orderId")
        if not order_id:
            return ProviderRefusal(
                code="bad_response",
                message="Bitwire: missing orderId",
                raw=envelope,
            )

        # Either phoneNumber (SBP) or cardNumber (card) is populated.
        phone = envelope.get("phoneNumber")
        card = envelope.get("cardNumber")
        account_number = str(phone or card or "")
        method_ours = (
            PaymentMethod.SBP if phone else (PaymentMethod.CARD if card else fallback_method)
        )

        amount_fiat = (
            self.safe_decimal(envelope.get("amount"))
            or self.safe_decimal(order_data.get("amount"))
            or Decimal("0")
        )
        provider_rate = self.safe_decimal(envelope.get("currencyRate"))

        expires_at = self.parse_iso8601(envelope.get("timeExpires"))
        if expires_at is None:
            expires_at = self.default_expires_at(provider)

        return ProviderRequisiteResponse(
            external_order_id=str(order_id),
            bank_name=str(envelope.get("issuer") or ""),
            account_number=account_number,
            # Blank when absent — never the provider name (would leak the
            # provider / cascade to the merchant).
            account_holder=str(envelope.get("holderName") or ""),
            payment_method=method_ours,
            payment_option_code=envelope.get("issuer"),
            amount_fiat=amount_fiat,
            expires_at=expires_at,
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
        resp = await self.safe_request(
            provider=provider,
            method="POST",
            path=f"/api/merchant/order/{external_order_id}/cancel",
            body=None,
            timeout_ms=timeout_ms,
            log_event="cancel_failed",
        )
        # Docs say "we expect any 2xx code — body is ignored". 404 means
        # the order is already gone (good enough for race-cancel).
        if resp is None:
            return False
        return 200 <= resp.status_code < 300 or resp.status_code == 404

    async def notify_receipt(
        self,
        *,
        provider: CascadeProvider,
        external_order_id: str,
        receipt_path: str,
        comment: Optional[str],
    ) -> bool:
        """Bitwire has no receipt-forward endpoint — confirmation flows
        from their trader-side through the callback. Return True.
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
        """Bitwire disputes are operator-initiated on their side; we have
        no merchant-side dispute API. Return True so the outer flow
        proceeds — the actual dispute will be raised through their
        support channel and we'll receive the DISPUTE status callback.
        """
        return True

    def parse_callback(
        self,
        *,
        provider: CascadeProvider,
        headers: Dict[str, str],
        body: bytes,
        query_params: Optional[Mapping[str, str]] = None,
    ) -> ParsedCallback:
        """Bitwire delivers callbacks as ``GET ?id=...&status=...``.

        The router populates ``query_params``; ``body`` is empty bytes.
        No signature: docs don't define a webhook auth scheme (the
        X-Secret-Phrase we ship at create-time encrypts the *response*
        body but isn't documented enough to verify here).
        """
        params = query_params or {}
        external_id = params.get("id")
        if not external_id:
            raise CallbackVerificationError("Bitwire callback missing id")

        status = self.parse_provider_status(params.get("status"))

        # Reconciliation fields only present on COMPLETED-after-DISPUTE
        # callbacks (see Bitwire's "callback flow" docs).
        reconciliation_amount = self.safe_decimal(
            params.get("reconciliationAmount")
        )
        reconciliation_rate = self.safe_decimal(params.get("reconciliationRate"))
        reconciliation_sum = self.safe_decimal(params.get("reconciliationSum"))

        raw_payload: Dict[str, Any] = {
            "query_params": dict(params),
        }
        if reconciliation_amount is not None:
            raw_payload["reconciliationAmount"] = str(reconciliation_amount)
        if reconciliation_rate is not None:
            raw_payload["reconciliationRate"] = str(reconciliation_rate)
        if reconciliation_sum is not None:
            raw_payload["reconciliationSum"] = str(reconciliation_sum)

        paid_amount: Optional[Decimal] = None
        if status == ProviderStatus.SUCCESS:
            # Use the reconciled fiat amount when present, else None
            # (Bitwire doesn't echo the original amount on basic callbacks).
            paid_amount = reconciliation_amount

        return ParsedCallback(
            external_order_id=str(external_id),
            status=status,
            raw=raw_payload,
            paid_amount_fiat=paid_amount,
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
        return self.safe_decimal(envelope.get("balance"))
