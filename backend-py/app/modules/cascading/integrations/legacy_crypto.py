"""LegacyCrypto cascade adapter.

Provider docs: payment-legacy.com Merchant API v1.

Signing scheme matches ``ProviderAdapter`` defaults exactly:
  outbound: ``hex(HMAC_SHA256(token, "{ts}.{METHOD}.{path}{canonical_json}"))``
  inbound:  ``hex(HMAC_SHA256(webhook_secret, raw_body))``

For GET requests the canonical body is the literal "{}". For receipt upload
(multipart) the canonical body is JSON of the *identifiers only*
(``{"payin_public_id": "PI-abc"}``), not the file contents — handled by
``request_signed(files=..., form_data=..., body=ids_body)``.

Per-provider settings (CascadeProvider.settings):
  bank_code_map         — our PaymentOption.code → their client_bank
                          (default: uppercase the code, sber → SBER)
  preferred_method_map  — our PaymentMethod → their preferred_method
                          (default: SBP → SBP, CARD → Card)
  expires_default_seconds — TTL when response carries no expires_at (def. 1800)

callback_url is auto-built from ``PROJECT_BASE_URL`` + the cascade
webhook path (``/api/cascade/v1/callbacks/<provider_code>``); there is
no per-provider override.
"""
from __future__ import annotations

import json
from decimal import Decimal
from typing import Any, Dict, List, Optional
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

# NOTE: The bank-alias normalisation map used to live here. It's been
# promoted to ``base.DEFAULT_BANK_RESPONSE_ALIASES`` because every
# cascade provider returns the bank as free text with the same dozen
# spellings of Sber/Tinkoff/Alfa/etc — having one shared map means
# admins maintain only one list, and new adapters get correct bank
# resolution for free. To extend per-provider, set
# ``settings.bank_response_aliases``; to extend per-adapter-class, set
# ``BANK_RESPONSE_ALIASES`` on the adapter subclass.


class LegacyCryptoAdapter(ProviderAdapter):
    code = "legacy_crypto"
    display_name = "LegacyCrypto"
    description = (
        "P2P-площадка payment-legacy.com. Поддерживает Card и SBP. "
        "Курс берётся из их ответа (rate_with_commission) — это их курс с их "
        "комиссией; для нашего курса переключите rate_source на «Наш курс»."
    )
    supports_provider_rate = True

    SUPPORTED_METHODS = (PaymentMethod.SBP, PaymentMethod.CARD)

    # ─── Аутентификация: ключи, которые админ заполняет в LK ───
    CREDENTIALS_SCHEMA = (
        AdapterFieldSpec(
            key="api_secret",
            label="API secret (Bearer + signing key)",
            type="string",
            secret=True,
            required=True,
            description=(
                "Секрет из кабинета LegacyCrypto. Используется и как "
                "Bearer-токен в Authorization, и как ключ для HMAC-SHA256 "
                "подписи тела запроса."
            ),
        ),
        AdapterFieldSpec(
            key="webhook_secret",
            label="Webhook secret",
            type="string",
            secret=True,
            description=(
                "Секрет для верификации входящих webhook'ов: "
                "X-Signature = hex(HMAC-SHA256(secret, raw_body))."
            ),
        ),
    )

    # ─── Declarative method/bank mappings ───
    DEFAULT_METHOD_MAP = {
        PaymentMethod.SBP.value: "SBP",
        PaymentMethod.CARD.value: "Card",
    }
    METHOD_MAP_SETTING_KEY = "preferred_method_map"  # legacy naming
    BANK_CODE_TRANSFORM = "upper"
    REQUIRED_CREDENTIALS = ("api_secret",)

    PROVIDER_STATUS_MAP = {
        "Processing": ProviderStatus.CREATED,
        "Working": ProviderStatus.PENDING,
        "Waiting": ProviderStatus.PAID,
        "Confirmed": ProviderStatus.SUCCESS,
        "Canceled": ProviderStatus.CANCELED,
    }
    # Issue-time liveness whitelist. Per the provider, the ONLY status at which
    # legacy has actually linked a real, usable requisite is ``Working`` (→PENDING).
    # Every other status — ``Processing`` (still matching, no requisite linked yet),
    # ``Waiting``/PAID, ``Confirmed``/SUCCESS, ``Canceled``/CANCELED — and any
    # unknown/missing one is a refusal at issue time (see parse_payin_response):
    # otherwise a non-Working response that still carries linked_requests +
    # public_id is read as a false WON and can win the race with a dead/absent
    # requisite.
    _LIVE_ISSUE_STATUSES = frozenset({ProviderStatus.PENDING})

    # Provider's spec requires non-empty values for these fields on every
    # payin request. When the caller didn't supply real client payment
    # instrument data (admin test-issue, or production cascade where we
    # don't collect the end-client's phone/card up-front), we fall back
    # to a mask-shaped placeholder per the provider's docs:
    #   SBP  → 11-digit phone, e.g. 89099199993
    #   Card → 16-digit PAN,   e.g. 2222333344445555
    # Both are overridable per-provider via settings below.
    _DEFAULT_REQUISITES_SBP = "89991112233"
    _DEFAULT_REQUISITES_CARD = "2222333344445555"
    # Provider rejects requests with empty/missing ``client_bank``. They
    # don't actually validate the value — per provider support, any
    # non-empty placeholder works. Used when neither ``payment_option_code``
    # resolves to a bank nor ``default_client_bank`` is configured.
    _DEFAULT_BANK_PLACEHOLDER = "Bank"

    SETTINGS_SCHEMA = (
        AdapterFieldSpec(
            key="sub_direction",
            label="sub_direction",
            type="select",
            default="payin_simple",
            options=[
                AdapterFieldOption("payin_simple", "payin_simple"),
                AdapterFieldOption("payin_check", "payin_check"),
            ],
            description=(
                "Какой режим запрашивать на /requests/simple/. payin_simple — "
                "обычный payin с резервированием реквизита. payin_check — режим "
                "проверки матчинга (см. документацию LegacyCrypto)."
            ),
        ),
        AdapterFieldSpec(
            key="expires_default_seconds",
            label="TTL реквизита (сек)",
            type="number",
            default=1800,
            min=60,
            max=86400,
            description="Используется, если провайдер не сообщил expires_at.",
        ),
        AdapterFieldSpec(
            key="preferred_method_map",
            label="Маппинг методов",
            type="kv_map",
            value_type="select",
            options=[
                AdapterFieldOption("Card", "Card"),
                AdapterFieldOption("SBP", "SBP"),
                AdapterFieldOption("All", "All (любой)"),
            ],
            description=(
                "Если задано — работает как whitelist. Ключи: наши коды методов "
                "(sbp / card). Значения: что отправлять в preferred_method "
                "LegacyCrypto. Пусто = sbp→SBP, card→Card."
            ),
        ),
        AdapterFieldSpec(
            key="bank_code_map",
            label="Маппинг банков",
            type="kv_map",
            value_type="string",
            description=(
                "Ключ — наш PaymentOption.code (например sber), значение — их "
                "client_bank (например SBER). Пусто = uppercase нашего кода."
            ),
        ),
        AdapterFieldSpec(
            key="bank_code_int_map",
            label="Числовые ID банков (client_bank_code)",
            type="kv_map",
            value_type="string",
            description=(
                "Опционально. Ключ — наш PaymentOption.code (например sber), "
                "значение — integer ID банка из таблицы LegacyCrypto (например "
                "\"1\" для Сбера). Если задано — отправляем поле client_bank_code "
                "рядом с client_bank. Если пусто — поле не отправляется (по "
                "спеке оно опционально)."
            ),
        ),
        AdapterFieldSpec(
            key="bank_response_aliases",
            label="Доп. алиасы payout_bank (наш код → варианты написания)",
            type="kv_map",
            value_type="string",
            description=(
                "Расширение базового маппинга вариаций. Ключ — наш "
                "PaymentOption.code (например sber), значение — варианты "
                "написания через запятую (например \"Сберъ, SberPay\"). "
                "Базовый словарь зашит в коде (Сбер/Альфа/ВТБ/...) — "
                "сюда добавляйте только новые написания, которых там ещё нет."
            ),
        ),
        AdapterFieldSpec(
            key="default_client_bank",
            label="Дефолтный client_bank (override плейсхолдера)",
            type="string",
            description=(
                "Что отправлять в client_bank, когда у заказа нет "
                "payment_option_code. По умолчанию шлём строку-плейсхолдер "
                "('Bank') — провайдер не валидирует значение, поле просто "
                "должно быть непустым. Заполняйте только если нужен "
                "осмысленный банк (например 'SBER')."
            ),
            placeholder="SBER",
        ),
        AdapterFieldSpec(
            key="default_client_bank_code",
            label="Дефолтный client_bank_code (числовой, fallback)",
            type="number",
            description=(
                "Числовой ID банка из таблицы LegacyCrypto — fallback, "
                "когда payment_option_code не передан и нет совпадения в "
                "bank_code_int_map."
            ),
            placeholder="1",
        ),
        AdapterFieldSpec(
            key="default_client_requisites_sbp",
            label="Дефолт client_requisites для SBP",
            type="string",
            description=(
                "Что отправлять в client_requisites для метода SBP, когда "
                "заказ не содержит реальных реквизитов клиента. По умолчанию "
                "11-значный телефон по маске (89099199993)."
            ),
            placeholder="89099199993",
        ),
        AdapterFieldSpec(
            key="default_client_requisites_card",
            label="Дефолт client_requisites для Card",
            type="string",
            description=(
                "То же самое для метода Card: 16-значный номер карты по маске. "
                "По умолчанию 2222333344445555."
            ),
            placeholder="2222333344445555",
        ),
    )

    # ─── ProviderAdapter interface ────────────────────────────
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
        callback_url = self.build_cascade_callback_url(provider.code)
        body: Dict[str, Any] = {
            "merchant_request_id": order_data.get("merchant_request_id") or idempotency_key,
            # Configurable: spec's ``/requests/simple/`` accepts both
            # ``payin_simple`` (reserves a requisite) and ``payin_check``
            # (matching-availability probe). Default stays at the former
            # to keep existing deployments unchanged.
            "sub_direction": str(settings.get("sub_direction") or "payin_simple"),
            "client_requisites": self._resolve_client_requisites(
                order_data.get("client_requisites"), method, settings
            ),
            "preferred_method": method_value,
            "client_amount": self.format_amount(order_data["amount"]),
            "client_full_name": str(order_data.get("client_full_name") or "Client"),
            "callback_url": str(callback_url),
        }

        option_code = order_data.get("payment_option_code")
        bank = self.resolve_bank_code(provider, option_code) if option_code else None
        if not bank:
            bank = str(
                settings.get("default_client_bank")
                or self._DEFAULT_BANK_PLACEHOLDER
            )
        body["client_bank"] = bank

        return PayinRequest(
            http_method="POST",
            path="/api/v1/merchant/requests/simple/",
            body=body,
        )

    def _resolve_client_requisites(
        self,
        provided: Optional[str],
        method: PaymentMethod,
        settings: Dict[str, Any],
    ) -> str:
        """Provider rejects empty ``client_requisites``. Use caller's value
        when present; otherwise synthesise a mask-shaped placeholder per
        method (configurable via per-provider settings).
        """
        if provided:
            return str(provided)
        if method == PaymentMethod.CARD:
            return str(
                settings.get("default_client_requisites_card")
                or self._DEFAULT_REQUISITES_CARD
            )
        # SBP (and any other supported method) → phone-shaped placeholder.
        return str(
            settings.get("default_client_requisites_sbp")
            or self._DEFAULT_REQUISITES_SBP
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
            path="/api/v1/merchant/payin/cancel/",
            body={"public_id": external_order_id},
            timeout_ms=timeout_ms,
            log_event="cancel_network_error",
        )
        # 200 OK or 404 (already gone) both mean "nothing to do".
        return resp is not None and resp.status_code in (200, 404)

    async def notify_receipt(
        self,
        *,
        provider: CascadeProvider,
        external_order_id: str,
        receipt_path: str,
        comment: Optional[str],
    ) -> bool:
        if not receipt_path:
            return True
        # Per the OpenAPI spec, receipt/upload requires only Authorization +
        # X-Timestamp + X-Signature (no X-Idempotency-Key). HMAC is computed
        # over JSON of the provided identifier only: {"payin_public_id":"…"}.
        resp = await self.upload_file(
            provider=provider,
            method="POST",
            path="/api/v1/merchant/receipt/upload/",
            file_path=receipt_path,
            file_field="file",
            body={"payin_public_id": external_order_id},
            form_data={"payin_public_id": external_order_id},
            log_event="receipt_upload_failed",
        )
        return resp is not None and resp.status_code == 201

    async def raise_dispute(
        self,
        *,
        provider: CascadeProvider,
        external_order_id: str,
        reason: str,
        evidence_paths: List[str],
    ) -> bool:
        # LegacyCrypto v1 has no dedicated dispute endpoint — escalations are
        # handled out-of-band. We still upload evidence so the provider's
        # support team has it attached to the payin record.
        ok = True
        for path in evidence_paths or []:
            if not await self.notify_receipt(
                provider=provider,
                external_order_id=external_order_id,
                receipt_path=path,
                comment=reason,
            ):
                ok = False
        return ok

    def verify_and_decode_callback(
        self,
        *,
        provider: CascadeProvider,
        headers: Dict[str, str],
        body: bytes,
    ) -> Dict[str, Any]:
        """The provider's ``MatchingWebhook`` delivers UNSIGNED callbacks — no
        ``X-Signature`` header. So inbound verification is OPTIONAL here: if the
        provider ever does send a signature AND a webhook secret is configured,
        we still verify it (defense in depth); otherwise we accept the unsigned
        body. (Outbound requests stay HMAC-signed with ``api_secret`` — only the
        provider's inbound webhook is unsigned.) Protect this endpoint at the
        edge with an IP allowlist if stronger guarantees are needed.
        """
        secret = self.get_webhook_signing_secret(provider)
        if secret and self._lookup_header(headers, self.SIGNATURE_HEADER):
            self.verify_callback_signature(secret=secret, headers=headers, body=body)
        return self.decode_callback_body(body)

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
        event = payload.get("event") or ""
        data = payload.get("data") or {}

        if event.startswith("payin."):
            external_id = data.get("payin_public_id")
            status_raw = data.get("payin_status")
        elif event.startswith("payout."):
            external_id = data.get("payout_public_id")
            status_raw = data.get("payout_status")
        else:
            raise CallbackVerificationError(f"Unknown event {event!r}")

        if not external_id:
            raise CallbackVerificationError("Missing public_id in callback")

        status = self.parse_provider_status(status_raw)

        paid_amount: Optional[Decimal] = None
        if status == ProviderStatus.PAID:
            paid_str = data.get("payin_rub_amount")
            if paid_str:
                try:
                    paid_amount = Decimal(str(paid_str))
                except (ValueError, TypeError):
                    paid_amount = None

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
            path=f"/api/v1/merchant/payin/{external_order_id}/",
            timeout_ms=timeout_ms,
            log_event="poll_failed",
        )
        if resp is None or resp.status_code != 200:
            return None
        envelope = self.json_or_none(resp) or {}
        data = envelope.get("data") or {}
        status = self.try_parse_provider_status(data.get("status"))
        if status is None:
            return None
        return ParsedCallback(
            external_order_id=str(data.get("public_id") or external_order_id),
            status=status,
            raw={"event": "polled", "data": data},
        )

    # ─── Provider-specific helpers ─────────────────────────────
    # _preferred_method / _bank_code / _build_issue_body inherited from
    # base as resolve_method_value / resolve_bank_code / build_payin_request.
    # Bank-name normalisation lives in
    # ``base.ProviderAdapter.resolve_payment_option_from_bank_name``.

    def _response_business_error(
        self, *, request_type: str, status: int, response_text: str
    ) -> Optional[str]:
        """Record a payin whose order isn't live (status != ``Working``) as an
        ERROR in the provider-request log instead of OK: a 201 carrying
        ``Canceled`` (or Confirmed/Waiting/Processing) gave us NO usable requisite
        — we refuse it in ``parse_payin_response`` — so it must not read as a
        successful provider call. Payin requests only; best-effort on the body."""
        if request_type != "payin":
            return None
        try:
            data = (json.loads(response_text) or {}).get("data") or {}
        except (ValueError, TypeError):
            return None
        status_raw = data.get("status")
        if self.try_parse_provider_status(status_raw) not in self._LIVE_ISSUE_STATUSES:
            return f"non-live status: {status_raw!r}"
        return None

    def parse_payin_response(
        self,
        *,
        provider: CascadeProvider,
        resp,
        order_data: Dict[str, Any],
        fallback_method: PaymentMethod,
    ) -> IssueResult:
        envelope = self.parse_envelope_or_refusal(
            resp,
            ok_statuses=(201,),  # LegacyCrypto returns 201, not 200, on success
            bad_response_message="LegacyCrypto issue: invalid JSON",
        )
        if isinstance(envelope, ProviderRefusal):
            return envelope
        payload = envelope.get("data") or {}

        # Accept only the one status where legacy has linked a real requisite for
        # the client — ``Working`` (→PENDING). Any other status (Processing /
        # Waiting / Confirmed / Canceled …) or an unknown/missing one is NOT a
        # usable requisite even if the body still carries linked_requests +
        # public_id; refuse so the cascade moves to the next provider instead of
        # winning with a dead/absent requisite.
        if self.try_parse_provider_status(payload.get("status")) not in self._LIVE_ISSUE_STATUSES:
            return ProviderRefusal(
                code="no_capacity",
                message=f"LegacyCrypto: order not live at issue (status={payload.get('status')!r})",
                raw=payload,
            )

        linked_list = payload.get("linked_requests") or []
        linked = linked_list[0] if linked_list else None
        if not linked:
            # No matching reserved → soft refusal; cascade keeps trying.
            return ProviderRefusal(
                code="no_capacity",
                message="LegacyCrypto: no linked matching",
                raw=payload,
            )

        public_id = payload.get("public_id")
        if not public_id:
            return ProviderRefusal(
                code="bad_response",
                message="LegacyCrypto issue: missing public_id",
                raw=payload,
            )

        method_ours = self.coerce_payment_method(
            linked.get("payout_preferred_method"), default=fallback_method
        )
        provider_rate = self.safe_decimal(payload.get("rate_with_commission"))
        amount_fiat = (
            self.safe_decimal(payload.get("rub_amount"))
            or self.safe_decimal(order_data["amount"])
            or Decimal("0")
        )

        # Resolve which PaymentOption the response actually points at —
        # in a cascade the provider may issue a different bank than we
        # requested. Prefer the response value (after alias-normalization
        # via the shared base helper) so the saved requisite lines up
        # with reality; fall back to the request-side code only when the
        # response string is unknown.
        payout_bank_raw = linked.get("payout_bank")
        option_code = self.resolve_payment_option_from_bank_name(
            payout_bank_raw, provider=provider,
        ) or order_data.get("payment_option_code")

        return ProviderRequisiteResponse(
            external_order_id=str(public_id),
            bank_name=str(payout_bank_raw or ""),
            account_number=str(linked.get("payout_requisites") or ""),
            # Blank when absent — never the provider name (would leak the
            # provider / cascade to the merchant).
            account_holder=str(
                linked.get("payout_full_name")
                or linked.get("payout_additional_requisites")
                or ""
            ),
            payment_method=method_ours,
            payment_option_code=option_code,
            amount_fiat=amount_fiat,
            expires_at=self.default_expires_at(provider),
            raw=payload,
            provider_rate=provider_rate,
        )
