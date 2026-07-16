"""Mock provider adapter for tests, dev sandboxing, and as a reference impl.

Behavior is driven entirely by ``CascadeProvider.settings`` (a JSONB column on
the provider row). This lets test scaffolding configure simulated latency,
forced refusals, signature secret etc. without touching code.

Recognized settings:
  * issue_delay_ms: int   — how long issue_requisite blocks before responding
  * refuse: bool          — return ProviderRefusal instead of a requisite
  * refuse_code: str      — refusal code (default "no_capacity")
  * cancel_succeeds: bool — cancel_request return value (default True)
  * receipt_succeeds: bool — notify_receipt return value (default True)
  * default_account: dict — bank_name/account_number/account_holder shown to client
  * webhook_secret: str   — used by parse_callback signature check (overrides
                            the encrypted webhook_secret on the provider)
"""
from __future__ import annotations

import asyncio
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

from app.common.enums.cascading import ProviderStatus
from app.common.enums.payments import PaymentMethod
from app.common.types import utcnow
from app.modules.cascading.integrations.base import (
    AdapterFieldOption,
    AdapterFieldSpec,
    CallbackVerificationError,
    IssueResult,
    ParsedCallback,
    ProviderAdapter,
    ProviderRefusal,
    ProviderRequisiteResponse,
)
from app.modules.cascading.models import CascadeProvider


class MockProviderAdapter(ProviderAdapter):
    code = "mock"
    display_name = "Mock (для тестов)"
    description = (
        "Заглушка для разработки и e2e-тестов. Поведение управляется полями "
        "формы ниже без реальных HTTP-запросов."
    )
    supports_provider_rate = False

    # Distinct header so the mock and real adapters can coexist behind the
    # same router in test sandboxes without colliding on X-Signature.
    SIGNATURE_HEADER = "X-Mock-Signature"

    SUPPORTED_METHODS = (PaymentMethod.SBP, PaymentMethod.CARD, PaymentMethod.SIM)

    SETTINGS_SCHEMA = (
        AdapterFieldSpec(
            key="supported_methods",
            label="Поддерживаемые методы",
            type="multi_select",
            options=[
                AdapterFieldOption("sbp", "СБП"),
                AdapterFieldOption("card", "Карта"),
                AdapterFieldOption("sim", "SIM"),
            ],
            description="Пусто = поддерживать всё.",
        ),
        AdapterFieldSpec(
            key="issue_delay_ms",
            label="Задержка перед выдачей (ms)",
            type="number",
            default=0,
            min=0,
            max=60000,
            description="Полезно для тестов race-логики каскада.",
        ),
        AdapterFieldSpec(
            key="refuse",
            label="Всегда отказывать",
            type="boolean",
            default=False,
        ),
        AdapterFieldSpec(
            key="refuse_code",
            label="Код отказа",
            type="string",
            default="no_capacity",
            placeholder="no_capacity",
        ),
        AdapterFieldSpec(
            key="webhook_secret",
            label="Webhook secret (для тестов подписи)",
            type="string",
            secret=True,
        ),
        AdapterFieldSpec(
            key="default_account",
            label="Реквизит по умолчанию",
            type="kv_map",
            value_type="string",
            description="bank_name / account_number / account_holder",
        ),
        AdapterFieldSpec(
            key="cancel_succeeds",
            label="Cancel возвращает True",
            type="boolean",
            default=True,
        ),
        AdapterFieldSpec(
            key="receipt_succeeds",
            label="Receipt forward возвращает True",
            type="boolean",
            default=True,
        ),
        AdapterFieldSpec(
            key="upstream_balance",
            label="Возвращать как upstream-баланс (USDT)",
            type="number",
            description="Подставляется в get_balance() для отладки UI.",
        ),
    )

    def supports(
        self,
        *,
        provider: CascadeProvider,
        method: PaymentMethod,
        payment_option_code: Optional[str],
    ) -> bool:
        supported_methods = self.get_settings(provider).get("supported_methods")
        if supported_methods is None:
            return True
        return method.value in supported_methods

    async def issue_requisite(
        self,
        *,
        provider: CascadeProvider,
        order_data: Dict[str, Any],
        idempotency_key: str,
        timeout_ms: int,
    ) -> IssueResult:
        cfg = self.get_settings(provider)
        delay_ms = int(cfg.get("issue_delay_ms", 0))
        if delay_ms:
            # Real adapters would just await the HTTP call; we sleep so race
            # tests can verify the cancel branch.
            await asyncio.sleep(delay_ms / 1000)

        # Synthesize a fake response so we route through parse_payin_response
        # exactly like the real adapters do. Refusal flag is encoded as a
        # non-2xx status on the fake envelope.
        fake_resp = _MockResponse(
            provider=provider, idempotency_key=idempotency_key, cfg=cfg
        )
        method = self.coerce_payment_method(order_data["payment_method"])
        return self.parse_payin_response(
            provider=provider,
            resp=fake_resp,
            order_data=order_data,
            fallback_method=method,
        )

    def parse_payin_response(
        self,
        *,
        provider: CascadeProvider,
        resp,
        order_data: Dict[str, Any],
        fallback_method: PaymentMethod,
    ) -> IssueResult:
        cfg = self.get_settings(provider)
        if cfg.get("refuse"):
            return ProviderRefusal(
                code=cfg.get("refuse_code", "no_capacity"),
                message=cfg.get("refuse_message", "mock refusal"),
                raw={"mock": True},
            )

        envelope = resp.json() if hasattr(resp, "json") else {}
        account = cfg.get("default_account") or {
            "bank_name": "Mock Bank",
            "account_number": "0000000000000000",
            "account_holder": "Mock Holder",
        }
        idempotency_key = envelope.get("idempotency_key", "mock")
        amount = Decimal(str(order_data["amount"]))
        return ProviderRequisiteResponse(
            external_order_id=f"mock-{idempotency_key}",
            bank_name=account["bank_name"],
            account_number=account["account_number"],
            account_holder=account["account_holder"],
            payment_method=fallback_method,
            payment_option_code=cfg.get("payment_option_code"),
            amount_fiat=amount,
            expires_at=utcnow() + timedelta(seconds=order_data.get("ttl_seconds", 1800)),
            raw={"mock": True, "idempotency_key": idempotency_key},
            provider_rate=None,
        )

    async def cancel_request(
        self,
        *,
        provider: CascadeProvider,
        external_order_id: str,
        timeout_ms: int,
    ) -> bool:
        return bool(self.get_settings(provider).get("cancel_succeeds", True))

    async def notify_receipt(
        self,
        *,
        provider: CascadeProvider,
        external_order_id: str,
        receipt_path: str,
        comment: Optional[str],
    ) -> bool:
        return bool(self.get_settings(provider).get("receipt_succeeds", True))

    async def raise_dispute(
        self,
        *,
        provider: CascadeProvider,
        external_order_id: str,
        reason: str,
        evidence_paths: List[str],
    ) -> bool:
        return bool(self.get_settings(provider).get("dispute_succeeds", True))

    async def get_balance(
        self,
        *,
        provider: CascadeProvider,
        timeout_ms: Optional[int] = None,
    ) -> Optional[Decimal]:
        """Read ``upstream_balance`` from settings — tests dial the value
        directly without round-tripping through a fake HTTP endpoint.
        """
        raw = self.get_settings(provider).get("upstream_balance")
        if raw is None:
            return None
        try:
            return Decimal(str(raw))
        except (InvalidOperation, TypeError, ValueError):
            return None

    def get_webhook_signing_secret(self, provider: CascadeProvider) -> Optional[str]:
        """Mock prefers an in-memory secret from settings (useful for tests),
        falling back to whatever is encrypted on the provider row."""
        return (
            self.get_settings(provider).get("webhook_secret")
            or super().get_webhook_signing_secret(provider)
        )

    def parse_callback(
        self,
        *,
        provider: CascadeProvider,
        headers: Dict[str, str],
        body: bytes,
    ) -> ParsedCallback:
        # Signature verification is delegated to the base class — we only
        # changed which header to read (SIGNATURE_HEADER = "X-Mock-Signature").
        payload = self.verify_and_decode_callback(
            provider=provider, headers=headers, body=body
        )

        try:
            status = ProviderStatus(payload["status"])
        except (KeyError, ValueError) as exc:
            raise CallbackVerificationError(
                f"Missing or unknown status in callback: {exc}"
            )

        external_order_id = payload.get("external_order_id") or payload.get("id")
        if not external_order_id:
            raise CallbackVerificationError("Missing external_order_id")

        return ParsedCallback(
            external_order_id=str(external_order_id),
            status=status,
            raw=payload,
        )


class _MockResponse:
    """Tiny httpx.Response-shaped object so Mock can route through
    ``parse_payin_response`` without spinning up real HTTP plumbing.

    Carries just the bits ``parse_payin_response`` reads — ``status_code``
    and ``.json()`` — and exposes the cfg/idempotency_key via the envelope.
    """

    def __init__(self, *, provider: CascadeProvider, idempotency_key: str, cfg: Dict[str, Any]):
        self.status_code = 200
        self._envelope = {
            "mock": True,
            "idempotency_key": idempotency_key,
            "provider_id": provider.id,
            "cfg_snapshot": dict(cfg),
        }
        self.text = ""

    def json(self) -> Dict[str, Any]:
        return self._envelope
