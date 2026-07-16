"""Provider adapter contract.

Every external provider plugs in via a subclass of ``ProviderAdapter``. The
adapter is the only place that knows about the provider's API quirks: HTTP
schema, signature scheme, status-code mapping, idempotency conventions, etc.

Adapters are stateless — they receive the ``CascadeProvider`` row on each call
so credentials / endpoints can be hot-rotated without restarting workers.

To add a new provider:
  1. Subclass ``ProviderAdapter`` in ``app/modules/cascading/integrations/<name>.py``
  2. Set class attrs: ``code``, ``display_name``, ``description``,
     ``supports_provider_rate``, ``SETTINGS_SCHEMA``
  3. Implement the abstract methods
  4. Register the adapter in ``registry.py``

The admin UI reads ``settings_schema()`` to render an adapter-specific form —
no JSON textareas, no per-provider frontend code.

Signing & webhook verification have **default HMAC-SHA256 implementations** on
the base class. New adapters whose signing scheme matches LegacyCrypto's
(``"{ts}.{METHOD}.{path}{canonical_json}"`` for outbound; raw body for
inbound) get signing/verification for free. To deviate, override a single
hook — see SIGNATURE_ALGORITHM / SIGNATURE_ENCODING / SIGNATURE_HEADER /
TIMESTAMP_HEADER class attrs, or override ``build_signature_payload`` /
``verify_callback_signature``.
"""
from __future__ import annotations

import base64
import contextvars
import hashlib
import hmac
import json
import logging
import time
from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Union

import httpx

from app.common.enums.cascading import ProviderStatus
from app.common.enums.orders import OrderStatus
from app.common.enums.payments import PaymentMethod
from app.common.types import utcnow
from app.modules.receipts.storage import ReceiptStorage
from app.core.security import decrypt_api_secret
from app.modules.cascading.models import CascadeProvider


_log = logging.getLogger(__name__)


# Semantic type of the provider HTTP call currently in flight — "payin",
# "cancel", "balance", "check", "upload", … The high-level caller sets it via
# the ``provider_request_type`` scope; ``request_signed`` reads it once at the
# logging chokepoint, so every adapter's request is tagged without threading a
# parameter through every layer. Adapter instances are shared singletons, so a
# contextvar (per-task) — not an instance attr — is the concurrency-safe carrier.
_PROVIDER_REQUEST_TYPE: contextvars.ContextVar[str] = contextvars.ContextVar(
    "provider_request_type", default="other"
)


@contextmanager
def provider_request_type(value: str):
    """Tag every provider HTTP request made inside this scope with ``value``.

    Set as close to the semantic operation as possible (e.g. around
    ``adapter.issue_requisite`` → "payin"). Propagates across ``await`` within
    the task and into ``asyncio.wait_for`` sub-tasks (they copy the context).
    """
    token = _PROVIDER_REQUEST_TYPE.set(value or "other")
    try:
        yield
    finally:
        _PROVIDER_REQUEST_TYPE.reset(token)


# ─── adapter settings schema ────────────────────────────────


@dataclass
class AdapterFieldOption:
    """One <option> in a select-type field."""

    value: str
    label: str


@dataclass
class AdapterFieldSpec:
    """One field of an adapter's settings form.

    The admin UI uses this to render the right control (input / select /
    boolean / kv_map etc.) instead of asking the user to write JSON by hand.

    Field types understood by the frontend renderer:
      * ``string``     — single-line text input
      * ``textarea``   — multi-line text area
      * ``url``        — text input with URL validation hint
      * ``number``     — numeric input
      * ``boolean``    — checkbox
      * ``select``     — dropdown (requires ``options``)
      * ``multi_select`` — multiple-choice (requires ``options``); stored as list
      * ``kv_map``     — list of (key, value) pairs; stored as dict.
                         When ``value_type='select'`` the value is itself a
                         dropdown sourced from ``options``.
    """

    key: str
    label: str
    type: str
    required: bool = False
    default: Any = None
    description: str = ""
    placeholder: str = ""
    secret: bool = False
    options: List[AdapterFieldOption] = field(default_factory=list)
    value_type: str = "string"  # for kv_map: type of the value side
    min: Optional[float] = None
    max: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        # Drop empty/default fields so the UI gets a clean shape.
        if not data["options"]:
            data.pop("options")
        return data


@dataclass
class AdapterInfo:
    """Public adapter descriptor exposed via GET /api/v1/cascade/adapters."""

    code: str
    display_name: str
    description: str
    supports_provider_rate: bool
    settings_schema: List[Dict[str, Any]] = field(default_factory=list)
    supported_methods: List[str] = field(default_factory=list)
    # Per-adapter credentials form (Authorization tab). Same field-spec
    # shape as ``settings_schema``; ``key`` is the column we write into
    # on the ``CascadeProvider`` row — one of ``api_key``,
    # ``api_secret``, ``webhook_secret``. Anything else under ``settings``
    # belongs in ``settings_schema``.
    credentials_schema: List[Dict[str, Any]] = field(default_factory=list)
    # Optional logo filename relative to ``frontend-vue/public/logos/``
    # (e.g. ``"swifty.svg"``). Empty string when the adapter has no
    # logo bundled. The admin UI renders a small badge with the icon
    # next to the adapter name in the dropdown and providers table.
    logo_filename: str = ""


@dataclass
class ProviderRequisiteResponse:
    """Successful issue_requisite response — provider returned a requisite."""

    external_order_id: str
    bank_name: str
    account_number: str
    account_holder: str
    payment_method: PaymentMethod
    payment_option_code: Optional[str]
    amount_fiat: Decimal
    expires_at: datetime
    raw: Dict[str, Any] = field(default_factory=dict)
    # Provider-specific rate at issue time (USDT per fiat). When None,
    # CascadingService falls back to provider.rates[currency].
    provider_rate: Optional[Decimal] = None


@dataclass
class ProviderRefusal:
    """Provider explicitly declined to issue a requisite. Not an error."""

    code: str  # short, machine-readable: no_capacity, amount_out_of_range, ...
    message: str
    raw: Dict[str, Any] = field(default_factory=dict)


# Type alias for adapter return values.
IssueResult = Union[ProviderRequisiteResponse, ProviderRefusal]


@dataclass
class PayinRequest:
    """Describes the HTTP call ``issue_requisite`` should make.

    Returned by ``build_payin_request``. The Template-Method
    ``issue_requisite`` in the base feeds this into ``request_signed``
    verbatim. Keeps adapter code small: subclass only has to declare what
    the call looks like, not how to dispatch it.
    """

    http_method: str  # "POST" | "PATCH" | "GET" — uppercase
    path: str  # path component, leading slash
    body: Optional[Dict[str, Any]] = None
    extra_headers: Optional[Dict[str, str]] = None
    params: Optional[Dict[str, Any]] = None
    # Overrides the idempotency_key passed to ``issue_requisite``. Most
    # adapters leave this None and the cascade-scheduler-provided key
    # flows through to ``IDEMPOTENCY_HEADER`` (if the provider supports
    # the header at all).
    idempotency_key: Optional[str] = None


@dataclass
class ParsedCallback:
    """Provider webhook parsed into a portable shape."""

    external_order_id: str
    status: ProviderStatus
    raw: Dict[str, Any] = field(default_factory=dict)
    # Optional details — adapters fill what they have.
    paid_amount_fiat: Optional[Decimal] = None
    received_at: Optional[datetime] = None
    refund_amount_fiat: Optional[Decimal] = None
    dispute_reason: Optional[str] = None


# Default mapping ProviderStatus → OrderStatus. Adapters can override
# `map_status_to_order_status` if their semantics differ.
_DEFAULT_STATUS_MAP: Dict[ProviderStatus, OrderStatus] = {
    ProviderStatus.CREATED: OrderStatus.CREATED,
    ProviderStatus.PENDING: OrderStatus.PENDING,
    ProviderStatus.PAID: OrderStatus.RECEIPT_UPLOADED,
    ProviderStatus.DISPUTED: OrderStatus.DISPUTED,
    ProviderStatus.SUCCESS: OrderStatus.SUCCESS,
    ProviderStatus.CANCELED: OrderStatus.CANCELED,
    ProviderStatus.FAILED: OrderStatus.FAILED,
    ProviderStatus.EXPIRED: OrderStatus.FAILED,
    ProviderStatus.REFUNDED: OrderStatus.REFUNDED,
}


# ─── Shared bank-name alias map (our code → list of spellings) ────────
#
# Different providers return the bank as free-text and ship many
# spellings of the same bank (Cyrillic, Latin, with/without "Банк"
# suffix, mixed case). The canonical form below groups all known
# spellings under a single ``PaymentOption.code`` — easier to scan,
# maintain and extend than the inverse "spelling → code" map.
#
# Spellings are stored case-sensitively as they were observed (so
# they're readable for humans on review). At lookup time we normalise
# both the response string and each spelling via ``normalize_bank_name``
# (lowercase + collapse whitespace), so duplicates differing only in
# case/whitespace are harmless.
#
# This map is **shared across all cascade providers** — they all see the
# same Russian banking landscape and admins shouldn't have to repeat the
# Sber/Tinkoff/Alfa aliases per integration. Layered overrides:
#   1. Per-provider via ``settings.bank_response_aliases`` (kv_map:
#      our code → "alias1, alias2, …" CSV).
#   2. Per-adapter-class via ``BANK_ALIASES_BY_CODE`` class-attr (same
#      shape as below).
#   3. ``DEFAULT_BANK_ALIASES_BY_CODE`` — these defaults.
DEFAULT_BANK_ALIASES_BY_CODE: Dict[str, List[str]] = {
    "sber": ["Сбербанк", "Сбер", "Sberbank", "Sber"],
    "yoomoney": ["Юмани", "Юmoney"],
    "jandeks-bank": ["Яндекс банк", "Яндекс"],
    "tbank": [
        "Тбанк", "Т Банк", "Т банк",
        "Тинькофф", "Тиньк",
        "Tinkoff", "TBank", "Tbank",
    ],
    "sovkom": ["Совкомбанк"],
    "ozon": ["Ozon банк", "Озон банк", "Озон Банк", "Озон", "Ozon"],
    "alfa": ["Альфа-банк", "Альфа банк", "Альфабанк", "Альфа"],
    "gazprom": ["Газпромбанк", "Газпром"],
    "vtb": ["ВТБ", "Втб"],
    # Rocketbank — deprecated but still appears in legacy data feeds.
    "rocketbank": ["Рокет"],
    "crediteurope": ["Кредит Европа Банк", "Кредит Европа"],
    "wbbank": ["Вайлдберис", "Wildberries банк", "Wildberries bank", "Wildberries"],
    "1cupis": ["Цупис"],
    "akbars": ["АК Барс", "АК барс", "Ак барс"],
    "bksbank": ["БКС", "Бкс"],
    "ingo": ["Инго", "Ингосстрах"],
    # МТС Банк — Card/SBP. MTS the telco lives under "mts" (SIM only).
    "mtsbank": ["МТС банк", "МТС Банк", "МТС"],
    "otp": ["ОТП банк", "Отп банк"],
    "pochta": ["Почта банк", "Почта"],
    "psb": ["Промсвязьбанк", "ПСБ", "Псб"],
    "raiffeisen": ["Райф", "Райффайзенбанк"],
    "rshb": ["Россельхозбанк"],
    "rsb": ["Русский стандарт"],
    "uralsib": ["УРАЛСИБ", "Уралсиб"],
}


def normalize_bank_name(raw: Any) -> str:
    """Canonicalise a free-text bank string for alias lookup.

    Lowercase + collapse internal whitespace + strip ends. Applied to
    both the response value and every alias spelling on the way into
    the inverse-index built by ``_build_bank_alias_index``.
    """
    if not raw:
        return ""
    return " ".join(str(raw).lower().split())


def _build_bank_alias_index(
    *maps: Mapping[str, Sequence[str]],
) -> Dict[str, str]:
    """Flatten one or more ``{code: [aliases]}`` maps into a single
    ``{normalized_alias: code}`` lookup table.

    Later maps in the argument list **override** earlier ones — so the
    canonical lookup order is ``(defaults, class_attr, per_provider)``,
    fed in that order, and admin/per-provider entries win.
    """
    out: Dict[str, str] = {}
    for m in maps:
        for code, aliases in (m or {}).items():
            if not code:
                continue
            for alias in (aliases or ()):
                norm = normalize_bank_name(alias)
                if norm:
                    out[norm] = code
    return out


# Pre-built inverse index for the defaults — costs nothing at import,
# avoids rebuilding on every lookup. Per-class and per-provider layers
# are merged in by ``resolve_payment_option_from_bank_name``.
_DEFAULT_BANK_ALIAS_INDEX: Dict[str, str] = _build_bank_alias_index(
    DEFAULT_BANK_ALIASES_BY_CODE,
)


class CallbackVerificationError(Exception):
    """Raised by parse_callback when signature/auth fails. Becomes 401 in router."""


class ProviderAdapter(ABC):
    """Per-provider integration contract.

    Concrete adapters live in ``app.modules.cascading.integrations.<name>`` and
    register themselves in ``registry.py``. Tests can swap a provider's
    ``adapter_type`` to ``"mock"`` to bypass real network calls without changing
    the rest of the cascade machinery.
    """

    # Short string used by the registry and persisted in CascadeProvider.adapter_type.
    code: str = ""
    # Human-friendly name shown in the admin dropdown.
    display_name: str = ""
    # Free-text description shown next to the dropdown / on hover.
    description: str = ""
    # Optional logo filename in ``frontend-vue/public/logos/`` — e.g.
    # ``"swifty.svg"``. Empty string = no badge icon. The admin UI joins
    # this with ``/logos/`` to build the icon URL.
    logo_filename: str = ""
    # True when the provider quotes its own rate in the issue response
    # (rate_with_commission, fixed conversion, etc). When False the admin
    # MUST configure rate_source='platform' + rate_config_id on the provider.
    supports_provider_rate: bool = True
    # Declarative form schema — drives the admin UI's settings tab.
    SETTINGS_SCHEMA: Sequence[AdapterFieldSpec] = ()
    # Declarative credentials schema — drives the admin UI's
    # "Аутентификация" (Authentication) tab. Each field's ``key`` must be
    # one of ``api_key`` / ``api_secret`` / ``webhook_secret`` — those are
    # the encrypted columns on ``CascadeProvider``. Override per adapter
    # with provider-specific labels (Swifty's "X-Secret", BridgePay's
    # "X-Identity", Bitwire's password, etc.) so admins know exactly what
    # to paste where. Default = generic api_secret + optional webhook
    # secret, suitable for HMAC-Bearer providers like LegacyCrypto.
    CREDENTIALS_SCHEMA: Sequence[AdapterFieldSpec] = (
        AdapterFieldSpec(
            key="api_secret",
            label="API secret / token",
            type="string",
            secret=True,
            required=True,
            description=(
                "Основной credential провайдера. Хранится в "
                "``api_secret_encrypted``."
            ),
        ),
        AdapterFieldSpec(
            key="webhook_secret",
            label="Webhook secret",
            type="string",
            secret=True,
            description=(
                "Секрет для верификации входящих webhook'ов "
                "(``webhook_secret_encrypted``). Если провайдер шарит "
                "API-ключ для webhook — оставь пустым."
            ),
        ),
    )
    # Which of our PaymentMethod values this adapter can handle. Empty list
    # means "all of them"; the cascade scheduler combines this with the
    # per-provider settings filter on top.
    SUPPORTED_METHODS: Sequence[PaymentMethod] = ()

    # ─── Signing knobs (override per provider) ───
    # Hash algorithm name as accepted by hashlib (sha1 / sha256 / sha512).
    SIGNATURE_ALGORITHM: str = "sha256"
    # How to encode the HMAC digest in the resulting header value.
    SIGNATURE_ENCODING: str = "hex"  # "hex" | "base64"
    # Header names used by sign_request() / verify_callback_signature() defaults.
    # Empty string = don't emit that header at all (useful for the token-only
    # providers like Swifty/Bitzone that want a single auth header on outbound).
    AUTH_HEADER: str = "Authorization"
    AUTH_SCHEME: str = "Bearer"
    TIMESTAMP_HEADER: str = "X-Timestamp"
    SIGNATURE_HEADER: str = "X-Signature"
    IDEMPOTENCY_HEADER: str = "X-Idempotency-Key"
    # Toggles HMAC-signing of outbound requests. When False, sign_request emits
    # only the auth header (+ optional idempotency) and skips the whole
    # timestamp/payload/signature dance — for providers that authenticate with
    # an API key alone (Swifty: X-Secret, Bitzone: x-api-key).
    SIGN_REQUESTS: bool = True
    # Where the webhook HMAC secret lives on the CascadeProvider row. Default
    # is the dedicated ``webhook_secret_encrypted`` column. Some providers
    # (Bitzone) reuse the API key for webhook signing — set "api_key" then.
    # Use "api_secret" for providers that sign webhooks with the same secret
    # used to sign outbound bodies.
    WEBHOOK_SECRET_SOURCE: str = "webhook_secret"  # "webhook_secret" | "api_key" | "api_secret"
    # When set, the default ``verify_callback_signature`` switches from
    # HMAC mode to **token-compare** mode: it just checks that the header
    # named here equals ``webhook_signing_secret`` byte-for-byte. Used by
    # providers that replay a pre-shared token on every webhook instead of
    # signing the body (BridgePay's X-Notification-Token, Payscrow's
    # X-API-Key). Leave "" for HMAC-style verification.
    WEBHOOK_TOKEN_HEADER: str = ""
    # When set, the default ``sign_request`` adds a second auth-style
    # header whose value is the provider's decrypted ``api_key``. Used by
    # providers that need **two independent credentials** in headers at
    # the same time — Bitwire ships ``Authorization: Bearer <JWT>`` AND
    # ``X-Api-Key: <static>`` on every call. Leave "" when one header is
    # enough; AUTH_HEADER alone handles the "primary" credential.
    EXTRA_AUTH_HEADER: str = ""

    # ─── Status / refusal mappings ───
    # Provider's status-string → our ProviderStatus enum.
    # Override in the subclass; parse_provider_status() uses this map.
    PROVIDER_STATUS_MAP: Dict[str, ProviderStatus] = {}
    # HTTP status code → refusal code string used by refusal_from_response().
    HTTP_STATUS_TO_REFUSAL: Dict[int, str] = {
        400: "bad_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        409: "idempotency_conflict",
        413: "payload_too_large",
        422: "validation_error",
        429: "rate_limited",
    }

    # ─── Declarative method / bank mappings ───
    # Default mapping our PaymentMethod.value → provider's method code.
    # Read by ``resolve_method_value`` when the per-provider setting under
    # ``METHOD_MAP_SETTING_KEY`` is empty. The setting acts as a closed
    # whitelist when populated (missing methods become "unsupported").
    DEFAULT_METHOD_MAP: Dict[str, str] = {}
    METHOD_MAP_SETTING_KEY: str = "method_map"

    # Reverse mapping — provider's method string → our PaymentMethod —
    # consulted by ``resolve_payment_method_from`` while parsing the response.
    # Used as a hint; falls back to ``fallback_method`` when key is missing.
    PROVIDER_METHOD_TO_OURS: Dict[str, PaymentMethod] = {}

    # How to default-transform our PaymentOption.code when no entry is
    # present in the per-provider bank map. Options: "upper" | "lower" |
    # "identity". Used by ``resolve_bank_code``.
    BANK_CODE_TRANSFORM: str = "upper"
    BANK_MAP_SETTING_KEY: str = "bank_code_map"
    # Optional per-class extension of ``DEFAULT_BANK_ALIASES_BY_CODE`` —
    # same ``{our_code: [spelling1, spelling2, …]}`` shape. Used for
    # provider-specific quirks (e.g. one integration returns "Сберъ" with
    # the hard sign). Default empty; the shared defaults cover the common
    # cases. Spellings are case-insensitive and whitespace-tolerant.
    BANK_ALIASES_BY_CODE: Dict[str, Sequence[str]] = {}
    # Settings key for the per-provider extension. Admin types it via a
    # kv_map field where keys are our internal PaymentOption.code and
    # values are comma-separated lists of new spellings to map to that
    # code. Final lookup precedence (highest wins): settings > class-attr
    # > defaults.
    BANK_ALIASES_SETTING_KEY: str = "bank_response_aliases"

    # ─── Pre-flight config validation ───
    # List of ``settings`` keys that must be non-empty before we even attempt
    # ``issue_requisite``. Each missing key short-circuits to a uniform
    # ``ProviderRefusal(code="misconfigured")``. Override per provider.
    REQUIRED_SETTINGS: Sequence[str] = ()
    # Credentials that must be present on the provider row before we try a
    # request. Values: "api_secret" (token), "api_key", "webhook_secret".
    # Order matters only for the error message.
    REQUIRED_CREDENTIALS: Sequence[str] = ()

    @classmethod
    def describe(cls) -> AdapterInfo:
        return AdapterInfo(
            code=cls.code,
            display_name=cls.display_name or cls.code,
            description=cls.description or "",
            supports_provider_rate=cls.supports_provider_rate,
            settings_schema=[f.to_dict() for f in cls.SETTINGS_SCHEMA],
            supported_methods=[m.value for m in cls.SUPPORTED_METHODS],
            credentials_schema=[f.to_dict() for f in cls.CREDENTIALS_SCHEMA],
            logo_filename=cls.logo_filename or "",
        )

    # ─── Signing primitives (HMAC-SHA256 / hex by default) ───
    #
    # Subclasses get a working signing & verification pipeline for free.
    # Most providers only need to override either AUTH_SCHEME / SIGNATURE_*
    # class attrs, or one of build_signature_payload / verify_callback_signature
    # — the rest composes around it.

    def canonical_json(self, body: Optional[Mapping[str, Any]]) -> str:
        """Default canonicalization: sort keys, no whitespace, ``{}`` for empty.

        Matches the rule documented in LegacyCrypto's OpenAPI spec and is a
        sensible default for any provider that signs canonical JSON bodies.
        Override when the provider expects a different format (e.g. plain
        ``str(body)`` or raw bytes).
        """
        if body is None or body == {}:
            return "{}"
        return json.dumps(body, separators=(",", ":"), sort_keys=True, ensure_ascii=False)

    def build_signature_payload(
        self,
        *,
        timestamp: str,
        method: str,
        path: str,
        body_json: str,
    ) -> str:
        """Return the string to feed into HMAC for an outbound request.

        Default layout: ``"{ts}.{METHOD}.{path}{canonical_json}"`` (LegacyCrypto
        style). Override for providers that prefer e.g. newline-separated parts
        or omit the path.
        """
        return f"{timestamp}.{method.upper()}.{path}{body_json}"

    def compute_signature(self, *, secret: str, payload: Union[str, bytes]) -> str:
        """HMAC the payload with ``secret`` using SIGNATURE_ALGORITHM.

        Returns the digest encoded per SIGNATURE_ENCODING. Concrete adapters
        rarely need to override this — the knobs are the class attrs.
        """
        try:
            algo = getattr(hashlib, self.SIGNATURE_ALGORITHM)
        except AttributeError as exc:
            raise ValueError(
                f"Unknown SIGNATURE_ALGORITHM={self.SIGNATURE_ALGORITHM!r}"
            ) from exc
        data = payload.encode("utf-8") if isinstance(payload, str) else payload
        digest = hmac.new(secret.encode("utf-8"), data, algo).digest()
        if self.SIGNATURE_ENCODING == "base64":
            return base64.b64encode(digest).decode("ascii")
        if self.SIGNATURE_ENCODING == "hex":
            return digest.hex()
        raise ValueError(
            f"Unknown SIGNATURE_ENCODING={self.SIGNATURE_ENCODING!r}"
        )

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
        """Build the auth/signature headers required for an outbound HTTP call.

        Default scheme (LegacyCrypto-compatible):
          Authorization: ``{AUTH_SCHEME} {token}``
          X-Timestamp:   unix seconds
          X-Signature:   ``{SIG_ENC}(HMAC(token, build_signature_payload(...)))``
          X-Idempotency-Key: optional, only when caller passes it

        Knobs that turn the dance off (no override needed):
          ``SIGN_REQUESTS=False``      — skip timestamp+signature entirely
                                         (Swifty's X-Secret, Bitzone's x-api-key)
          ``AUTH_HEADER=""``           — emit no auth header
          ``TIMESTAMP_HEADER=""``      — skip timestamp even when signing
          ``SIGNATURE_HEADER=""``      — skip signature header even when signing
          ``IDEMPOTENCY_HEADER=""``    — provider has no idempotency channel

        ``provider`` is passed through by ``request_signed`` so overrides can
        reach ``provider.base_url`` (for signing full URLs) or
        ``provider.api_key_encrypted`` (for dual-credential schemes where the
        bearer/secret signs and a separate identifier goes into a header —
        BridgePay-style X-Identity + X-Signature). Default implementation
        ignores it.

        Adapters that need extra static headers (X-Api-Client, X-Vendor) can
        pass ``extra_headers`` or override this method.
        """
        headers: Dict[str, str] = {}
        if self.AUTH_HEADER:
            headers[self.AUTH_HEADER] = (
                f"{self.AUTH_SCHEME} {token}" if self.AUTH_SCHEME else token
            )

        # Secondary auth header — auto-populated from provider.api_key when
        # ``EXTRA_AUTH_HEADER`` is declared. Used by providers that need two
        # parallel credentials in headers (Bitwire: Bearer + X-Api-Key).
        if self.EXTRA_AUTH_HEADER and provider is not None:
            extra_value = self.get_api_key(provider)
            if extra_value:
                headers[self.EXTRA_AUTH_HEADER] = extra_value

        if self.SIGN_REQUESTS:
            timestamp = str(int(time.time()))
            body_json = self.canonical_json(body)
            signing_payload = self.build_signature_payload(
                timestamp=timestamp, method=method, path=path, body_json=body_json
            )
            signature = self.compute_signature(secret=token, payload=signing_payload)
            if self.TIMESTAMP_HEADER:
                headers[self.TIMESTAMP_HEADER] = timestamp
            if self.SIGNATURE_HEADER:
                headers[self.SIGNATURE_HEADER] = signature

        if idempotency_key and self.IDEMPOTENCY_HEADER:
            headers[self.IDEMPOTENCY_HEADER] = idempotency_key
        if extra_headers:
            headers.update(extra_headers)
        return headers

    def verify_callback_signature(
        self,
        *,
        secret: str,
        headers: Mapping[str, str],
        body: bytes,
    ) -> None:
        """Verify an inbound webhook's signature.

        Two modes, picked declaratively:

          1. **HMAC mode** (default) — provider sends ``SIGNATURE_HEADER``
             containing ``compute_signature(secret, body)`` of the raw body.
          2. **Token-compare mode** — ``WEBHOOK_TOKEN_HEADER`` is set; we
             just check the named header equals ``secret`` byte-for-byte.
             Useful for providers that replay a pre-shared token on each
             webhook instead of signing the body (BridgePay's
             X-Notification-Token, Payscrow's X-API-Key).

        On mismatch we raise ``CallbackVerificationError`` (which the public
        callback router translates to a 401 response).

        Override when a provider signs ``{ts}.{body}``, JWT-style nested
        envelopes, or anything more exotic.
        """
        if self.WEBHOOK_TOKEN_HEADER:
            received = self._lookup_header(headers, self.WEBHOOK_TOKEN_HEADER)
            if not received:
                raise CallbackVerificationError(
                    f"Missing {self.WEBHOOK_TOKEN_HEADER} header"
                )
            if not hmac.compare_digest(secret, received):
                raise CallbackVerificationError(
                    f"Invalid {self.WEBHOOK_TOKEN_HEADER} value"
                )
            return

        received = self._lookup_header(headers, self.SIGNATURE_HEADER)
        if not received:
            raise CallbackVerificationError(
                f"Missing {self.SIGNATURE_HEADER} header"
            )
        expected = self.compute_signature(secret=secret, payload=body)
        if not hmac.compare_digest(expected, received):
            raise CallbackVerificationError("Invalid webhook signature")

    @staticmethod
    def _lookup_header(headers: Mapping[str, str], name: str) -> Optional[str]:
        """Case-insensitive header lookup — HTTP clients normalize casing differently."""
        target = name.lower()
        for key, value in headers.items():
            if key.lower() == target:
                return value
        return None

    # ─── Credential & settings helpers ───
    #
    # Every adapter reads from ``CascadeProvider`` columns in the same way —
    # these helpers eliminate the per-adapter ``_resolve_token`` / ``_settings``
    # boilerplate. Override only when a provider needs something unusual
    # (e.g. credentials nested in settings JSON).

    @staticmethod
    def get_settings(provider: CascadeProvider) -> Dict[str, Any]:
        """Free-form ``provider.settings`` dict — null-safe."""
        return dict(provider.settings or {})

    @staticmethod
    def build_cascade_callback_url(provider_code: str) -> str:
        """Auto-built URL of our cascade webhook endpoint for a provider.

        Routes to ``app.api.cascade.v1.endpoints.callbacks.cascade_callback``
        (mounted at ``/api/cascade/v1/callbacks/{provider_code}`` in main.py).
        Each ``CascadeProvider`` row gets its own URL via its unique ``code``.

        Built from the ``PROJECT_BASE_URL`` env var. Returns "" when unset.
        """
        from app.core.config import get_settings as _app_settings

        base = (_app_settings().PROJECT_BASE_URL or "").rstrip("/")
        if not base:
            return ""
        return f"{base}/api/cascade/v1/callbacks/{provider_code}"

    @staticmethod
    def get_api_key(provider: CascadeProvider) -> Optional[str]:
        """Decrypted ``api_key`` (the public-ish identifier in most schemes)."""
        if not provider.api_key_encrypted:
            return None
        return decrypt_api_secret(provider.api_key_encrypted)

    @staticmethod
    def get_token(provider: CascadeProvider, *, required: bool = True) -> str:
        """Decrypted ``api_secret`` — the bearer token / signing key.

        Raises CallbackVerificationError when required and missing; the cascade
        scheduler treats the failure as a refusal and moves on, no 500s.
        """
        if not provider.api_secret_encrypted:
            if required:
                raise CallbackVerificationError(
                    f"Provider {provider.code!r} has no api_secret configured"
                )
            return ""
        return decrypt_api_secret(provider.api_secret_encrypted)

    @staticmethod
    def get_webhook_secret(provider: CascadeProvider) -> Optional[str]:
        """Decrypted webhook secret — returns None when absent or decrypt fails.

        Adapters typically pass this directly into verify_callback_signature;
        None means "accept unsigned webhooks" (dev/test mode).
        """
        if not provider.webhook_secret_encrypted:
            return None
        try:
            return decrypt_api_secret(provider.webhook_secret_encrypted)
        except Exception:
            return None

    def get_webhook_signing_secret(
        self, provider: CascadeProvider
    ) -> Optional[str]:
        """Resolve the HMAC secret used to verify inbound webhooks.

        Source is declared by ``WEBHOOK_SECRET_SOURCE`` — most providers use
        the dedicated webhook_secret column; some (Bitzone) reuse the API
        key, and a few share the outbound signing secret.

        Adapters with custom resolution (Mock reads from settings first) can
        still override this method directly.
        """
        source = self.WEBHOOK_SECRET_SOURCE
        if source == "api_key":
            return self.get_api_key(provider)
        if source == "api_secret":
            try:
                return self.get_token(provider, required=False) or None
            except Exception:
                return None
        return self.get_webhook_secret(provider)

    def verify_and_decode_callback(
        self,
        *,
        provider: CascadeProvider,
        headers: Mapping[str, str],
        body: bytes,
    ) -> Dict[str, Any]:
        """Standard webhook entry point: verify signature, then decode JSON.

        Pulls the webhook secret per ``WEBHOOK_SECRET_SOURCE``; when it's
        absent we skip the signature check (dev/test mode) instead of
        refusing. Adapters use this to collapse the verify+decode
        boilerplate that every parse_callback would otherwise repeat:

            payload = self.verify_and_decode_callback(
                provider=provider, headers=headers, body=body
            )
        """
        secret = self.get_webhook_signing_secret(provider)
        if secret:
            self.verify_callback_signature(
                secret=secret, headers=headers, body=body
            )
        return self.decode_callback_body(body)

    # ─── HTTP wrapper ───
    #
    # request_signed() handles the full outbound dance: extract token, build
    # signed headers, open httpx client, run the call, surface RequestError as
    # an exception the cascade scheduler will translate into ERROR/TIMEOUT.
    # For multipart uploads pass ``files=...`` and ``form_data=...`` instead
    # of ``body=...`` — the signature is still computed from ``body`` (the
    # identifier JSON), matching how every provider we've seen actually signs.

    async def acquire_token(self, provider: CascadeProvider) -> str:
        """Return the primary credential ``sign_request`` should treat as ``token``.

        Default: the static ``api_secret_encrypted`` decrypted via
        ``get_token``. Override for providers that mint short-lived
        tokens per session (Bitwire's sign-in flow returns a JWT with
        ``dateTimeExpires`` — the adapter caches it and refreshes on
        expiry, all inside this hook).

        Async so adapters can hit the auth endpoint without blocking the
        event loop and so a Redis-backed cache slot is easy to add later
        without re-shaping the contract.
        """
        return self.get_token(provider)

    async def request_signed(
        self,
        *,
        provider: CascadeProvider,
        method: str,
        path: str,
        body: Optional[Mapping[str, Any]] = None,
        idempotency_key: Optional[str] = None,
        timeout_ms: Optional[int] = None,
        extra_headers: Optional[Mapping[str, str]] = None,
        params: Optional[Mapping[str, Any]] = None,
        files: Optional[Mapping[str, Any]] = None,
        form_data: Optional[Mapping[str, Any]] = None,
    ) -> httpx.Response:
        """Run a signed HTTP request and return the raw response.

        Network/transport errors bubble up as ``httpx.RequestError`` —
        ``CascadingService._call_adapter_issue`` already catches and converts
        these into ``CascadeAttemptStatus.ERROR``.
        """
        token = await self.acquire_token(provider)
        headers = self.sign_request(
            token=token,
            method=method,
            path=path,
            body=body,
            idempotency_key=idempotency_key,
            extra_headers=extra_headers,
            provider=provider,
        )
        timeout_s = (timeout_ms or provider.request_timeout_ms) / 1000

        # multipart vs json: when files/form_data are present we MUST NOT
        # also send ``json=`` — httpx will refuse. The signature was computed
        # from the identifier body, which is correct for the upload endpoints
        # we've integrated so far.
        #
        # JSON bodies are serialized HERE (not via httpx's ``json=`` keyword)
        # so the exact bytes the adapter signs are the exact bytes the
        # transport ships. Otherwise providers that sign raw bodies (BridgePay)
        # see a mismatch — httpx's default encoder adds whitespace.
        request_kwargs: Dict[str, Any] = {"headers": dict(headers)}
        if params:
            request_kwargs["params"] = dict(params)
        if files is not None or form_data is not None:
            if files is not None:
                request_kwargs["files"] = files
            if form_data is not None:
                request_kwargs["data"] = dict(form_data)
        elif body is not None and method.upper() != "GET":
            content_bytes = self.serialize_request_body(body).encode("utf-8")
            request_kwargs["content"] = content_bytes
            request_kwargs["headers"].setdefault("Content-Type", "application/json")

        from app.modules.cascading.repository import record_provider_request

        full_url = f"{provider.base_url.rstrip('/')}/{path.lstrip('/')}"
        if request_kwargs.get("params"):
            from urllib.parse import urlencode
            full_url = f"{full_url}?{urlencode(request_kwargs['params'], doseq=True)}"
        if "content" in request_kwargs:
            req_body = request_kwargs["content"].decode("utf-8", "replace")
        elif "files" in request_kwargs or "data" in request_kwargs:
            req_body = "<multipart>"
        else:
            req_body = ""

        _t0 = time.perf_counter()
        _status = 0
        _resp_body = ""
        _success = False
        _error = ""
        try:
            async with httpx.AsyncClient(
                base_url=provider.base_url, timeout=timeout_s
            ) as client:
                resp = await client.request(method.upper(), path, **request_kwargs)
            _status = resp.status_code
            _resp_body = resp.text
            _success = 200 <= _status < 300
            if _success:
                # Let the adapter downgrade a 2xx to an ERROR in the provider-
                # request log when the body is a business failure (e.g. a terminal
                # / non-live order status). Best-effort; must not break the call.
                try:
                    _biz_error = self._response_business_error(
                        request_type=_PROVIDER_REQUEST_TYPE.get(),
                        status=_status,
                        response_text=_resp_body,
                    )
                except Exception:  # noqa: BLE001 — log classification must not break the provider call
                    _biz_error = None
                if _biz_error:
                    _success = False
                    _error = _biz_error
            return resp
        except httpx.RequestError as exc:
            _error = str(exc)
            _success = False
            raise
        finally:
            record_provider_request(
                provider=provider,
                method=method.upper(),
                url=full_url,
                headers=request_kwargs.get("headers", {}),
                body=req_body,
                status=_status,
                response_body=_resp_body,
                success=_success,
                error=_error,
                provider_latency_ms=round((time.perf_counter() - _t0) * 1000),
                request_type=_PROVIDER_REQUEST_TYPE.get(),
            )

    def _response_business_error(
        self, *, request_type: str, status: int, response_text: str
    ) -> Optional[str]:
        """Business-level failure hidden inside an HTTP-2xx provider response, used
        for the provider-request LOG's success flag («Результат»). Return a short
        reason string to record the call as an ERROR (not OK); return None to keep
        it OK.

        Default: None — any 2xx is OK. Overridden by adapters whose 2xx body can
        still be a business failure (e.g. a terminal/non-live order status). Only
        ever DOWNGRADES a 2xx; keep overrides defensive — the caller guards, but it
        must never raise."""
        return None

    def serialize_request_body(
        self, body: Optional[Mapping[str, Any]]
    ) -> str:
        """Render the request body as the exact string the transport will send.

        Default: ``canonical_json`` — sort_keys + no whitespace. Adapters that
        sign their own bytes (BridgePay) override this to drop sort_keys, or
        return a raw pre-serialized string. The contract is that whatever this
        method returns must be byte-for-byte identical to what ``sign_request``
        feeds into HMAC.
        """
        return self.canonical_json(body)

    async def safe_request(
        self,
        *,
        provider: CascadeProvider,
        method: str,
        path: str,
        body: Optional[Mapping[str, Any]] = None,
        idempotency_key: Optional[str] = None,
        timeout_ms: Optional[int] = None,
        extra_headers: Optional[Mapping[str, str]] = None,
        params: Optional[Mapping[str, Any]] = None,
        files: Optional[Mapping[str, Any]] = None,
        form_data: Optional[Mapping[str, Any]] = None,
        log_event: str = "request_failed",
    ) -> Optional[httpx.Response]:
        """``request_signed`` with the network error pattern handled once.

        Returns ``None`` on ``httpx.RequestError`` (the cascade scheduler can
        treat that as ERROR). Use for idempotent calls — cancel, poll, balance,
        receipt forward, dispute — where a transient blip shouldn't bubble up.

        ``issue_requisite`` should still call ``request_signed`` directly so
        the cascade attempt is stamped ERROR/TIMEOUT properly.
        """
        try:
            return await self.request_signed(
                provider=provider,
                method=method,
                path=path,
                body=body,
                idempotency_key=idempotency_key,
                timeout_ms=timeout_ms,
                extra_headers=extra_headers,
                params=params,
                files=files,
                form_data=form_data,
            )
        except httpx.RequestError as exc:
            _log.warning(
                "cascade_adapter_network_error",
                extra={
                    "adapter": self.code,
                    "event": log_event,
                    "provider_id": provider.id,
                    "method": method,
                    "path": path,
                    "error": str(exc),
                },
            )
            return None

    # MIME guesses used by ``file_to_data_url``. Adapters can extend by
    # overriding the method or passing ``mime_type=`` explicitly.
    _DEFAULT_MIME_BY_SUFFIX: Dict[str, str] = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".pdf": "application/pdf",
        ".heic": "image/heic",
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
    }

    @classmethod
    def file_to_data_url(
        cls,
        file_path: Optional[str],
        *,
        mime_type: Optional[str] = None,
    ) -> Optional[str]:
        """Read a file from disk, return ``"data:<mime>;base64,<payload>"``.

        Used by providers that accept receipts/disputes as base64-encoded
        JSON fields instead of multipart uploads (Garex's PATCH status flow).
        Returns ``None`` when the path is empty, the file is missing, or any
        IO error occurs — the cascade scheduler will surface that as a
        non-fatal forward failure.
        """
        if not file_path:
            return None
        f = Path(file_path)
        if not ReceiptStorage.is_within_upload_dir(file_path) or not f.exists():
            _log.warning(
                "cascade_adapter_data_url_file_missing",
                extra={"adapter": cls.code, "file_path": str(f)},
            )
            return None
        if mime_type is None:
            mime_type = cls._DEFAULT_MIME_BY_SUFFIX.get(
                f.suffix.lower(), "application/octet-stream"
            )
        try:
            raw = f.read_bytes()
        except OSError as exc:
            _log.warning(
                "cascade_adapter_data_url_read_failed",
                extra={"adapter": cls.code, "file_path": str(f), "error": str(exc)},
            )
            return None
        return f"data:{mime_type};base64,{base64.b64encode(raw).decode('ascii')}"

    async def upload_file(
        self,
        *,
        provider: CascadeProvider,
        method: str,
        path: str,
        file_path: Optional[str],
        file_field: str = "attachment",
        content_type: str = "application/octet-stream",
        form_data: Optional[Mapping[str, Any]] = None,
        body: Optional[Mapping[str, Any]] = None,
        idempotency_key: Optional[str] = None,
        timeout_ms: Optional[int] = None,
        params: Optional[Mapping[str, Any]] = None,
        log_event: str = "upload_failed",
    ) -> Optional[httpx.Response]:
        """Multipart upload with file-handle lifecycle handled once.

        - ``file_path`` falsy → call without files, useful for "open empty
          dispute" endpoints (Swifty allows this).
        - File missing on disk → logs and returns ``None``; the cascade
          attempt won't get stamped as success.
        - Always closes the file handle even when the request fails.
        - ``params=`` shoves arbitrary entries into the query string — used by
          providers that route the trade id there (Bitzone's ``?tradeId=``).
        """
        opened = None
        files = None
        if file_path:
            f = Path(file_path)
            # Containment guard first: never open a path that escapes UPLOAD_DIR.
            if not ReceiptStorage.is_within_upload_dir(file_path) or not f.exists():
                _log.warning(
                    "cascade_adapter_upload_file_missing",
                    extra={
                        "adapter": self.code,
                        "event": log_event,
                        "provider_id": provider.id,
                        "file_path": str(f),
                    },
                )
                return None
            opened = f.open("rb")
            files = {file_field: (f.name, opened, content_type)}

        try:
            return await self.safe_request(
                provider=provider,
                method=method,
                path=path,
                body=body,
                idempotency_key=idempotency_key,
                timeout_ms=timeout_ms,
                form_data=form_data,
                files=files,
                params=params,
                log_event=log_event,
            )
        finally:
            if opened is not None:
                opened.close()

    async def upload_files(
        self,
        *,
        provider: CascadeProvider,
        method: str,
        path: str,
        file_paths: Sequence[str],
        file_field: str = "files",
        content_type: str = "application/octet-stream",
        form_data: Optional[Mapping[str, Any]] = None,
        body: Optional[Mapping[str, Any]] = None,
        idempotency_key: Optional[str] = None,
        timeout_ms: Optional[int] = None,
        params: Optional[Mapping[str, Any]] = None,
        log_event: str = "upload_failed",
    ) -> Optional[httpx.Response]:
        """Multipart upload supporting **multiple files under one field name**.

        Httpx accepts a list of ``(field, (filename, fh, mime))`` tuples to
        send several files in one request — used by providers whose dispute
        endpoint takes ``files[]`` as a multipart array (Payscrow).

        Missing files are silently skipped (logged); if **all** are missing
        we still POST the form (some endpoints accept disputes without
        attachments). All handles are closed even when the request fails.
        """
        handles: List[Any] = []
        files: List[tuple] = []
        try:
            for fp in file_paths or []:
                if not fp:
                    continue
                f = Path(fp)
                if not ReceiptStorage.is_within_upload_dir(fp) or not f.exists():
                    _log.warning(
                        "cascade_adapter_upload_file_missing",
                        extra={
                            "adapter": self.code,
                            "event": log_event,
                            "provider_id": provider.id,
                            "file_path": str(f),
                        },
                    )
                    continue
                h = f.open("rb")
                handles.append(h)
                files.append((file_field, (f.name, h, content_type)))
            return await self.safe_request(
                provider=provider,
                method=method,
                path=path,
                body=body,
                idempotency_key=idempotency_key,
                timeout_ms=timeout_ms,
                form_data=form_data,
                files=files if files else None,
                params=params,
                log_event=log_event,
            )
        finally:
            for h in handles:
                try:
                    h.close()
                except Exception:  # pragma: no cover — best-effort cleanup
                    pass

    # ─── Response → result helpers ───

    def refusal_from_response(self, resp: httpx.Response) -> ProviderRefusal:
        """Build a typed ProviderRefusal from a non-success HTTP response.

        Maps the status code through ``HTTP_STATUS_TO_REFUSAL``, falls back to
        ``http_<code>``. Pulls the ``error``/``errors`` field out of the body
        if present.
        """
        try:
            data = resp.json()
            message = data.get("error") or data.get("errors") or resp.text[:200]
        except (json.JSONDecodeError, ValueError):
            data = {}
            message = resp.text[:200]
        code = self.HTTP_STATUS_TO_REFUSAL.get(
            resp.status_code, f"http_{resp.status_code}"
        )
        return ProviderRefusal(
            code=code,
            message=str(message),
            raw={"status": resp.status_code, "body": data},
        )

    @staticmethod
    def decode_callback_body(body: bytes) -> Dict[str, Any]:
        """Strict JSON body decoder for webhooks. Raises CallbackVerificationError
        on bad encoding / malformed JSON / non-object payloads.
        """
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CallbackVerificationError(f"Bad JSON body: {exc}")
        if not isinstance(payload, dict):
            raise CallbackVerificationError("Callback body must be a JSON object")
        return payload

    @staticmethod
    def json_or_none(resp: httpx.Response) -> Optional[Any]:
        """Decode the HTTP response body as JSON or return ``None`` on any failure.

        Saves the ``try: resp.json() except (ValueError, AttributeError): return None``
        boilerplate that we'd otherwise write in every poll/balance path.
        """
        try:
            return resp.json()
        except (ValueError, AttributeError):
            return None

    @staticmethod
    def safe_decimal(value: Any) -> Optional[Decimal]:
        """Decimal(str(value)) with all the parse errors swallowed → None.

        Use when surfacing the value to the caller and it's optional anyway
        (rate / paid_amount / balance). Returns None for ``None``, "", garbage.
        """
        if value is None or value == "":
            return None
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            return None

    @staticmethod
    def parse_iso8601(value: Optional[str]) -> Optional[datetime]:
        """Lenient ISO-8601 parse — handles "...Z" suffix and assumes UTC for naive."""
        if not value:
            return None
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    def default_expires_at(
        self,
        provider: CascadeProvider,
        *,
        fallback_seconds: int = 1800,
        settings_key: str = "expires_default_seconds",
    ) -> datetime:
        """Compute a fallback ``expires_at`` for cascade requisites.

        Reads ``settings[settings_key]`` (e.g. provider-configurable TTL) and
        falls back to ``fallback_seconds``. Always returns a timezone-aware
        datetime in UTC.
        """
        raw = self.get_settings(provider).get(settings_key)
        try:
            seconds = int(raw) if raw is not None else fallback_seconds
        except (TypeError, ValueError):
            seconds = fallback_seconds
        return utcnow() + timedelta(seconds=seconds)

    @staticmethod
    def coerce_payment_method(
        value: Any, *, default: Optional[PaymentMethod] = None
    ) -> PaymentMethod:
        """Convert random user input into a PaymentMethod enum.

        Strings are lowercased before lookup ("Card" → "card"). Pass a
        ``default`` to swallow unknown values; otherwise raises ValueError.
        """
        if isinstance(value, PaymentMethod):
            return value
        if isinstance(value, str):
            try:
                return PaymentMethod(value.lower())
            except ValueError:
                pass
        if default is not None:
            return default
        raise ValueError(f"Cannot coerce {value!r} to PaymentMethod")

    @staticmethod
    def format_amount(amount: Any) -> str:
        """Format an amount as a plain fixed-point decimal string ('10.00').

        Avoids scientific notation that Python's str(Decimal) sometimes emits
        for very small values. Returns "0" for None.
        """
        if amount is None:
            return "0"
        if isinstance(amount, Decimal):
            return format(amount, "f")
        try:
            return format(Decimal(str(amount)), "f")
        except (InvalidOperation, ValueError, TypeError):
            return str(amount)

    def parse_provider_status(self, raw: Optional[str]) -> ProviderStatus:
        """Lookup ``raw`` in ``PROVIDER_STATUS_MAP``; raise on miss.

        Adapters that want a softer behaviour (e.g. polling endpoints that
        may return an unknown intermediate state) should use
        ``try_parse_provider_status`` instead.
        """
        status = self.try_parse_provider_status(raw)
        if status is None:
            raise CallbackVerificationError(
                f"Unknown provider status {raw!r}; "
                f"declare it in PROVIDER_STATUS_MAP."
            )
        return status

    def try_parse_provider_status(self, raw: Optional[str]) -> Optional[ProviderStatus]:
        """Same as parse_provider_status but returns None on unknown values."""
        if raw is None:
            return None
        return self.PROVIDER_STATUS_MAP.get(raw)

    # ─── Declarative resolvers (use class-attr maps; cheap to override) ───

    def resolve_method_value(
        self, provider: CascadeProvider, method: PaymentMethod
    ) -> Optional[str]:
        """Our PaymentMethod → provider's method string.

        The per-provider setting under ``METHOD_MAP_SETTING_KEY`` (default
        ``"method_map"``) acts as a closed whitelist when populated; absent
        keys → unsupported. Falls back to ``DEFAULT_METHOD_MAP`` class-attr
        when the setting is empty / missing.
        """
        method_map = self.get_settings(provider).get(self.METHOD_MAP_SETTING_KEY)
        if method_map:
            return method_map.get(method.value)
        return self.DEFAULT_METHOD_MAP.get(method.value)

    def resolve_payment_method_from(
        self, raw: Any, *, default: PaymentMethod
    ) -> PaymentMethod:
        """Provider's method-string → our PaymentMethod.

        Consults ``PROVIDER_METHOD_TO_OURS`` class-attr; falls back to
        ``default`` when the key is missing or value is empty/garbage.
        """
        if not raw:
            return default
        mapped = self.PROVIDER_METHOD_TO_OURS.get(str(raw))
        return mapped or default

    def resolve_bank_code(
        self, provider: CascadeProvider, payment_option_code: Optional[str]
    ) -> Optional[str]:
        """Our PaymentOption.code → provider's bank string.

        Per-provider setting under ``BANK_MAP_SETTING_KEY`` overrides; when
        absent, the default falls back to ``code.upper() | code.lower() |
        code`` per ``BANK_CODE_TRANSFORM`` class-attr.
        """
        if not payment_option_code:
            return None
        bank_map = self.get_settings(provider).get(self.BANK_MAP_SETTING_KEY) or {}
        mapped = bank_map.get(payment_option_code)
        if mapped:
            return mapped
        transform = self.BANK_CODE_TRANSFORM
        if transform == "upper":
            return payment_option_code.upper()
        if transform == "lower":
            return payment_option_code.lower()
        return payment_option_code

    def resolve_payment_option_from_bank_name(
        self,
        raw_bank: Any,
        *,
        provider: Optional[CascadeProvider] = None,
    ) -> Optional[str]:
        """Provider's free-text bank label → our ``PaymentOption.code``.

        Used in ``parse_payin_response`` (and webhook parsers) when a
        provider hands back the realised requisite's bank as a string.
        In a cascade the provider may issue a different bank than we
        requested, so we should trust the response — but the response
        string is free-form and varies wildly in spelling.

        Lookup precedence (highest wins):
          1. ``settings.<BANK_ALIASES_SETTING_KEY>`` — admin-extended
             aliases per provider row.
          2. ``BANK_ALIASES_BY_CODE`` class-attr — adapter-shipped quirks.
          3. ``DEFAULT_BANK_ALIASES_BY_CODE`` — shared canonical map.

        Returns ``None`` when nothing matches; callers typically fall
        back to the request-side ``payment_option_code`` in that case.
        """
        norm = normalize_bank_name(raw_bank)
        if not norm:
            return None
        # Defaults are pre-indexed at import. Class-attr and provider
        # settings are rebuilt per call — they're tiny and the call is
        # cheap (alias lists rarely grow beyond a few dozen entries).
        if provider is not None:
            settings_map = self._parse_bank_aliases_setting(
                self.get_settings(provider).get(self.BANK_ALIASES_SETTING_KEY)
            )
            hit = _build_bank_alias_index(settings_map).get(norm)
            if hit:
                return hit
        if self.BANK_ALIASES_BY_CODE:
            hit = _build_bank_alias_index(self.BANK_ALIASES_BY_CODE).get(norm)
            if hit:
                return hit
        return _DEFAULT_BANK_ALIAS_INDEX.get(norm)

    @staticmethod
    def _parse_bank_aliases_setting(
        raw: Any,
    ) -> Dict[str, List[str]]:
        """Normalise the kv_map admin types into the ``{code: [aliases]}`` shape.

        The settings form is a kv_map where keys are our internal
        ``PaymentOption.code`` and values are free-form strings of
        spellings the admin wants this provider to recognise. We accept
        comma-, semicolon-, or newline-separated lists for ergonomic
        input ("Сбер, Сбербанк, SberPay" all in one cell).
        """
        if not raw or not isinstance(raw, dict):
            return {}
        out: Dict[str, List[str]] = {}
        for code, val in raw.items():
            if not code:
                continue
            if isinstance(val, (list, tuple)):
                aliases = [str(x) for x in val if x]
            else:
                text = str(val or "")
                # Split on common separators so a single cell can hold
                # multiple spellings.
                aliases = [
                    p.strip()
                    for p in text.replace(";", ",").replace("\n", ",").split(",")
                    if p.strip()
                ]
            if aliases:
                out[str(code)] = aliases
        return out

    # ─── Response envelope helpers ───

    def parse_envelope_or_refusal(
        self,
        resp: "httpx.Response",
        *,
        ok_statuses: Sequence[int] = (200, 201),
        bad_response_message: str = "Invalid JSON envelope",
    ) -> Union[Dict[str, Any], ProviderRefusal]:
        """Decode ``resp`` to a dict envelope or return a typed refusal.

        Collapses the four-line ``status_code in (200, 201) → json_or_none →
        isinstance dict`` dance that every ``parse_payin_response`` repeats.
        Returns:
          * ``Dict[str, Any]`` — happy path; caller proceeds to extract fields.
          * ``ProviderRefusal`` — non-OK status (mapped via
            ``refusal_from_response``) or bad/non-object JSON body
            (code=``"bad_response"``).
        """
        if resp.status_code not in ok_statuses:
            return self.refusal_from_response(resp)
        envelope = self.json_or_none(resp)
        if envelope is None or not isinstance(envelope, dict):
            return ProviderRefusal(
                code="bad_response",
                message=bad_response_message,
                raw={"status": resp.status_code, "body": resp.text[:500]},
            )
        return envelope

    # ─── Pre-flight config validation ───

    def validate_provider_config(
        self, provider: CascadeProvider
    ) -> Optional[ProviderRefusal]:
        """Check declared ``REQUIRED_SETTINGS`` and ``REQUIRED_CREDENTIALS``.

        Returns ``None`` when everything is in place; otherwise a uniform
        ``ProviderRefusal(code="misconfigured")`` listing what's missing.

        Adapters can override for richer per-field rules (e.g. "merchant_id
        must be numeric"), but the typical case is "this setting key must
        be non-empty" — covered by the declarative list.
        """
        cfg = self.get_settings(provider)
        missing_settings = [
            key
            for key in (self.REQUIRED_SETTINGS or ())
            if cfg.get(key) in (None, "", [], {})
        ]
        missing_credentials: List[str] = []
        for cred in self.REQUIRED_CREDENTIALS or ():
            if cred == "api_key" and not provider.api_key_encrypted:
                missing_credentials.append("api_key")
            elif cred == "api_secret" and not provider.api_secret_encrypted:
                missing_credentials.append("api_secret")
            elif cred == "webhook_secret" and not provider.webhook_secret_encrypted:
                missing_credentials.append("webhook_secret")
        if not missing_settings and not missing_credentials:
            return None
        parts = []
        if missing_settings:
            parts.append("settings: " + ", ".join(missing_settings))
        if missing_credentials:
            parts.append("credentials: " + ", ".join(missing_credentials))
        return ProviderRefusal(
            code="misconfigured",
            message=f"{self.code}: missing " + "; ".join(parts),
            raw={
                "missing_settings": missing_settings,
                "missing_credentials": missing_credentials,
            },
        )

    def supports(
        self,
        *,
        provider: CascadeProvider,
        method: PaymentMethod,
        payment_option_code: Optional[str],
    ) -> bool:
        """Cheap pre-check before issuing a request.

        Default rule:
          1. ``method`` is in ``SUPPORTED_METHODS`` (if declared),
          2. provider passes ``validate_provider_config`` (REQUIRED_SETTINGS
             + REQUIRED_CREDENTIALS),
          3. ``resolve_method_value(provider, method)`` returns non-None
             (so the per-provider method_map whitelist is respected).

        Override when an adapter needs even cheaper or extra cross-field
        checks. Returning False excludes the provider from a group/pool
        iteration with an "unsupported_method" refusal recorded in
        CascadeOrderAttempt — no network call is made.
        """
        if self.SUPPORTED_METHODS and method not in self.SUPPORTED_METHODS:
            return False
        if self.validate_provider_config(provider) is not None:
            return False
        return self.resolve_method_value(provider, method) is not None

    # ─── Pay-in request shape (build → call → parse) ───
    #
    # Default ``issue_requisite`` is a Template Method that:
    #   1. validates provider config (REQUIRED_SETTINGS/CREDENTIALS)
    #   2. coerces & resolves the payment method
    #   3. asks the subclass for a typed PayinRequest via build_payin_request
    #   4. runs the HTTP call through request_signed
    #   5. delegates parsing to parse_payin_response
    #
    # New adapters only need to implement ``build_payin_request`` +
    # ``parse_payin_response``. Adapters that don't fit (Mock, providers
    # with multi-step pay-in flow) can still override ``issue_requisite``
    # entirely.

    async def issue_requisite(
        self,
        *,
        provider: CascadeProvider,
        order_data: Dict[str, Any],
        idempotency_key: str,
        timeout_ms: int,
    ) -> IssueResult:
        """Default Template-Method workflow for pay-in issuance.

        Override only when the request shape can't be expressed as a single
        signed HTTP call (e.g. Mock, two-step issuance flows). Most adapters
        should implement just ``build_payin_request`` and
        ``parse_payin_response``.
        """
        refusal = self.validate_provider_config(provider)
        if refusal is not None:
            return refusal

        method = self.coerce_payment_method(order_data["payment_method"])
        method_value = self.resolve_method_value(provider, method)
        if method_value is None:
            return ProviderRefusal(
                code="unsupported_method",
                message=(
                    f"{self.code}: no method mapping for {method.value}"
                ),
            )

        request = self.build_payin_request(
            provider=provider,
            order_data=order_data,
            idempotency_key=idempotency_key,
            method=method,
            method_value=method_value,
        )
        if isinstance(request, ProviderRefusal):
            return request

        resp = await self.request_signed(
            provider=provider,
            method=request.http_method,
            path=request.path,
            body=request.body,
            idempotency_key=request.idempotency_key or idempotency_key,
            timeout_ms=timeout_ms,
            extra_headers=request.extra_headers,
            params=request.params,
        )
        return self.parse_payin_response(
            provider=provider,
            resp=resp,
            order_data=order_data,
            fallback_method=method,
        )

    def build_payin_request(
        self,
        *,
        provider: CascadeProvider,
        order_data: Dict[str, Any],
        idempotency_key: str,
        method: PaymentMethod,
        method_value: str,
    ) -> "Union[PayinRequest, ProviderRefusal]":
        """Build the HTTP call for ``issue_requisite``.

        Default implementation raises ``NotImplementedError``. Adapters
        that use the Template-Method ``issue_requisite`` MUST override
        this. Adapters that override ``issue_requisite`` directly may
        leave this method unimplemented.

        Return ``PayinRequest`` for a normal call, or ``ProviderRefusal``
        when something about the order can't be expressed as a request
        for this provider (extra per-order checks beyond the declarative
        ones in ``validate_provider_config``).

        ``method_value`` is the already-resolved provider-side method
        string (LegacyCrypto's ``preferred_method``, Bitzone's ``method``,
        BridgePay's ``paymentOption``, etc) — saves a re-lookup.
        """
        raise NotImplementedError(
            f"{type(self).__name__} must implement build_payin_request "
            "or override issue_requisite"
        )

    @abstractmethod
    def parse_payin_response(
        self,
        *,
        provider: CascadeProvider,
        resp: "httpx.Response",
        order_data: Dict[str, Any],
        fallback_method: PaymentMethod,
    ) -> IssueResult:
        """Translate the provider's HTTP pay-in response into a typed result.

        - ``resp`` is the raw ``httpx.Response`` from ``request_signed``.
        - Returning ``ProviderRequisiteResponse`` = success (provider issued
          a requisite the cascade can hand to the merchant).
        - Returning ``ProviderRefusal`` = soft failure (no capacity / amount
          out of range / unsupported method) — the cascade keeps trying
          remaining providers.

        Implementations should:
          1. Inspect ``resp.status_code`` and fall through to
             ``self.refusal_from_response(resp)`` for unexpected 4xx/5xx.
          2. Use ``self.json_or_none(resp)`` to decode the body safely.
          3. Detect soft refusals encoded inside 2xx responses (status flags,
             empty result blocks) and return ``ProviderRefusal``.
          4. Pull amount/rate via ``self.safe_decimal``, expiry via
             ``self.parse_iso8601`` + ``self.default_expires_at(provider)``
             as fallback, and resolve the realized payment method via
             ``self.coerce_payment_method(..., default=fallback_method)``.
        """

    @abstractmethod
    async def cancel_request(
        self,
        *,
        provider: CascadeProvider,
        external_order_id: str,
        timeout_ms: int,
    ) -> bool:
        """Tell the provider we no longer need the previously-issued requisite.

        Called for losers in a race. Best-effort: we only log the result.
        """

    @abstractmethod
    async def notify_receipt(
        self,
        *,
        provider: CascadeProvider,
        external_order_id: str,
        receipt_path: str,
        comment: Optional[str],
    ) -> bool:
        """Forward the merchant's receipt upload to the provider.

        SUCCESS/FAILED status only flips on the provider's callback — this is
        purely a "client says they paid" signal.
        """

    @abstractmethod
    async def raise_dispute(
        self,
        *,
        provider: CascadeProvider,
        external_order_id: str,
        reason: str,
        evidence_paths: List[str],
    ) -> bool:
        """Escalate to a dispute on the provider's side."""

    @abstractmethod
    def parse_callback(
        self,
        *,
        provider: CascadeProvider,
        headers: Dict[str, str],
        body: bytes,
        query_params: Optional[Mapping[str, str]] = None,
    ) -> ParsedCallback:
        """Validate the webhook signature and translate the payload.

        Raises ``CallbackVerificationError`` if the signature is wrong — the
        router converts that into a 401 response.

        ``query_params`` is set by the router for **GET-style callbacks**
        (Bitwire delivers status updates as
        ``GET {callbackUrl}?id=...&status=...``) — ``body`` will be empty
        in that case and the adapter reads its fields from query_params.
        For the usual POST-with-JSON pattern this stays ``None`` and
        adapters ignore it.
        """

    def map_status_to_order_status(self, status: ProviderStatus) -> OrderStatus:
        """Default status mapping — adapters can override per their semantics."""
        return _DEFAULT_STATUS_MAP[status]

    async def poll_status(
        self,
        *,
        provider: CascadeProvider,
        external_order_id: str,
        timeout_ms: int,
    ) -> Optional[ParsedCallback]:
        """Optional fallback for providers without reliable webhooks.

        Default: not implemented — return None. Override when the provider
        exposes a status-query endpoint and you want belt-and-suspenders polling.
        """
        return None

    async def get_balance(
        self,
        *,
        provider: CascadeProvider,
        timeout_ms: Optional[int] = None,
    ) -> Optional[Decimal]:
        """Return the merchant's balance held by this provider (USDT).

        Default: ``None`` — not all providers expose a balance endpoint.
        Override and call ``self.request_signed(...)`` when they do; the admin
        UI surfaces the value next to our locally-tracked virtual-trader
        balance so operators can reconcile drift without leaving the page.
        """
        return None
