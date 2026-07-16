"""Cascading service — the routing layer between OrderService and external providers.

Responsibilities:
  * Lifecycle of CascadeProvider rows: bootstrap virtual user+trader, encrypt
    credentials, expose CRUD for the admin API.
  * Build CascadeGroups, manage M:N links to providers and merchants.
  * try_cascade(merchant, order) — the main entry point called from
    OrderService when local pooling can't supply a requisite.
  * apply_callback(...) — translate a provider webhook into our order state
    transitions (delegates to OrderService.complete_order / fail_order).

Design notes:
  * Adapter calls run in parallel during a group race, but they never touch
    the SQLAlchemy session — adapters only do HTTP work. After the race
    finishes we persist all CascadeOrderAttempt rows from the main session
    serially. This keeps us inside the AsyncSession's "single-task" guarantee.
  * Materializing a winner creates a one-shot Requisite(source='cascade')
    pinned to the virtual user. The pooler ignores it (source filter) so it
    can never be reused for another order.
"""
from __future__ import annotations

import asyncio
import secrets
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.common.enums.balances import BalanceType, LedgerReferenceType
from app.common.enums.cascading import (
    CascadeAttemptStatus,
    CascadeMode,
    CascadeRateSource,
    ProviderStatus,
    RequisiteSource,
)
from app.common.enums.finances import Currency
from app.common.enums.orders import OrderStatus
from app.common.enums.payments import PaymentMethod
from app.common.enums.requisites import RequisiteStatus
from app.common.phone import is_phone_method, normalize_phone
from app.common.enums.traders import TraderStatus
from app.common.enums.users import UserRole
from app.common.types import utcnow
from app.core.exceptions import (
    NotFoundException,
    UnauthorizedException,
    ValidationException,
)
from app.core.logging import get_logger
from app.core.security import (
    decrypt_api_secret,
    encrypt_api_secret,
    get_password_hash,
)
from app.infrastructure.cache.redis import redis_client
from app.modules.base.service import BaseService
from app.modules.cascading.circuit_breaker import CircuitBreaker
from app.modules.cascading.integrations import registry
from app.modules.cascading.integrations.base import (
    CallbackVerificationError,
    IssueResult,
    ParsedCallback,
    ProviderAdapter,
    ProviderRefusal,
    ProviderRequisiteResponse,
    provider_request_type,
)
from app.modules.cascading.models import (
    CascadeGroup,
    CascadeOrderAttempt,
    CascadeProvider,
    CascadeProviderMetric,
    cascade_group_merchants,
    cascade_group_providers,
)
from app.modules.cascading.repository import (
    CascadeGroupRepository,
    CascadeOrderAttemptRepository,
    CascadeProviderMetricRepository,
    CascadeProviderRepository,
)
from app.modules.cascading.schemas import (
    CascadeGroupCreate,
    CascadeGroupUpdate,
    CascadeProviderCreate,
    CascadeProviderUpdate,
)
from app.modules.cascading.selectors import rank_pool_providers
from app.modules.merchants.models import Merchant
from app.modules.orders.models import Order
from app.modules.requisites.models import Requisite
from app.modules.traders.models import Trader
from app.modules.users.models import User

logger = get_logger(__name__)


# Header names we redact in the admin "request preview" payload. The
# preview is admin-only, but it ends up in screenshots, Sentry traces,
# and copy-paste debug messages — keep the leakage surface tiny.
# We still show the *first* 6 chars + length so operators can sanity-
# check that signing produced something non-empty.
_SENSITIVE_HEADER_NAMES = {
    "authorization",
    "x-api-key",
    "x-secret",
    "x-secret-phrase",
    "x-signature",
    "x-identity",
    "x-notification-token",
    "x-mock-signature",
    "x-hash",
}


def _mask_sensitive_headers(headers: Dict[str, str]) -> Dict[str, str]:
    """Return a copy of ``headers`` with credential-bearing values masked.

    Admin-facing debug output should let operators verify that:
      * the header is present
      * its value is non-empty
      * the prefix looks right (e.g. ``Bearer eyJh…``)

    …without copy-pasting a full JWT into Slack. Non-sensitive headers
    (Content-Type, X-Timestamp, X-Idempotency-Key) pass through verbatim.
    """
    masked: Dict[str, str] = {}
    for k, v in (headers or {}).items():
        if k.lower() in _SENSITIVE_HEADER_NAMES and v:
            head = v[:6]
            masked[k] = f"{head}… ({len(v)} chars)"
        else:
            masked[k] = v
    return masked


@dataclass
class _RawAttemptOutcome:
    """In-memory result of a single adapter call. Persisted later as CascadeOrderAttempt."""

    provider_id: int
    started_at: datetime
    finished_at: datetime
    latency_ms: int
    status: CascadeAttemptStatus
    idempotency_key: str
    refusal_reason: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    response: Optional[ProviderRequisiteResponse] = None


@dataclass
class CascadeResult:
    """Outcome of CascadingService.try_cascade — what OrderService gets back."""

    success: bool
    provider: Optional[CascadeProvider] = None
    attempt: Optional[CascadeOrderAttempt] = None
    requisite: Optional[Requisite] = None
    provider_fee_usdt: Decimal = Decimal("0")
    our_profit_usdt: Decimal = Decimal("0")
    diagnostics: List[Dict[str, Any]] = field(default_factory=list)


# ---------- public service ----------

class CascadingService(BaseService):
    def __init__(self, session: AsyncSession):
        super().__init__(session)
        self.providers = CascadeProviderRepository(session)
        self.groups = CascadeGroupRepository(session)
        self.attempts = CascadeOrderAttemptRepository(session)
        self.metrics = CascadeProviderMetricRepository(session)
        self.breaker = CircuitBreaker(session)

    # ------------------ provider CRUD ------------------

    async def create_provider(self, data: CascadeProviderCreate) -> CascadeProvider:
        if not registry.has(data.adapter_type):
            raise ValidationException(
                f"Unknown adapter_type={data.adapter_type!r}; "
                f"registered: {registry.list_codes()}"
            )
        if await self.providers.get_by_code(data.code):
            raise ValidationException(f"Provider code {data.code!r} already exists")

        await self._validate_rate_config(
            adapter_type=data.adapter_type,
            rate_source=data.rate_source,
            rate_config_id=data.rate_config_id,
        )

        # Bootstrap virtual user+trader. The role is TRADER + is_system=True so
        # finance / escrow flows treat it like any other trader, but trader
        # listings filter out is_system rows.
        username = f"cascade_{data.code}"
        existing_user = await self.session.execute(
            select(User).where(User.username == username)
        )
        if existing_user.scalars().first():
            raise ValidationException(
                f"System user {username!r} already exists — pick a different code"
            )

        async with self.session.begin_nested():
            virtual_user = User(
                username=username,
                password=get_password_hash(secrets.token_urlsafe(32)),
                role=UserRole.TRADER,
                is_system=True,
                is_blocked=False,
                use_shared_balance=True,
                totp_enabled=False,
            )
            self.session.add(virtual_user)
            await self.session.flush()

            virtual_trader = Trader(
                user_id=virtual_user.id,
                status=TraderStatus.ENABLED,
                is_payin_active=True,
                is_payout_active=False,
                accept_all_merchants=False,  # cascade traffic doesn't go through pooling
            )
            self.session.add(virtual_trader)
            await self.session.flush()

            provider = CascadeProvider(
                code=data.code,
                name=data.name,
                adapter_type=data.adapter_type,
                is_active=data.is_active,
                base_url=data.base_url,
                api_key_encrypted=encrypt_api_secret(data.api_key) if data.api_key else None,
                api_secret_encrypted=(
                    encrypt_api_secret(data.api_secret) if data.api_secret else None
                ),
                webhook_secret_encrypted=(
                    encrypt_api_secret(data.webhook_secret)
                    if data.webhook_secret
                    else None
                ),
                virtual_user_id=virtual_user.id,
                virtual_trader_id=virtual_trader.id,
                fees=dict(data.fees),
                rate_source=data.rate_source,
                rate_config_id=data.rate_config_id,
                min_amount_fiat=(
                    Decimal(str(data.min_amount_fiat))
                    if data.min_amount_fiat is not None
                    else None
                ),
                max_amount_fiat=(
                    Decimal(str(data.max_amount_fiat))
                    if data.max_amount_fiat is not None
                    else None
                ),
                cb_window_seconds=data.cb_window_seconds,
                cb_threshold_failures=data.cb_threshold_failures,
                cb_threshold_rate=data.cb_threshold_rate,
                cb_cooldown_seconds=data.cb_cooldown_seconds,
                request_timeout_ms=data.request_timeout_ms,
                cancel_timeout_ms=data.cancel_timeout_ms,
                priority_weight=data.priority_weight,
                settings=dict(data.settings),
            )
            self.session.add(provider)
            await self.session.flush()
            await self.session.refresh(provider)

            if data.initial_balance_usdt and data.initial_balance_usdt > 0:
                from app.modules.finance.service import FinanceService

                # Seed the virtual user's WORK balance through the ledger (not a
                # raw balance write) so the provider's books are traceable.
                await FinanceService(self.session).set_provider_balance(
                    virtual_user, Decimal(str(data.initial_balance_usdt)),
                    reason="Cascade provider initial balance",
                )

        await self.audit_log(
            action="cascade_provider_create",
            entity_type="cascade_provider",
            entity_id=provider.id,
            new_values={"code": provider.code, "adapter_type": provider.adapter_type},
        )
        return provider

    async def update_provider(
        self, provider_id: int, data: CascadeProviderUpdate
    ) -> CascadeProvider:
        provider = await self.providers.get(provider_id)
        if not provider:
            raise NotFoundException(f"Cascade provider {provider_id} not found")

        update_payload: Dict[str, Any] = {}
        for field_name in (
            "name",
            "adapter_type",
            "is_active",
            "base_url",
            "fees",
            "rate_source",
            "rate_config_id",
            "cb_window_seconds",
            "cb_threshold_failures",
            "cb_threshold_rate",
            "cb_cooldown_seconds",
            "request_timeout_ms",
            "cancel_timeout_ms",
            "priority_weight",
            "settings",
        ):
            value = getattr(data, field_name)
            if value is not None:
                update_payload[field_name] = value

        if data.adapter_type is not None and not registry.has(data.adapter_type):
            raise ValidationException(
                f"Unknown adapter_type={data.adapter_type!r}; "
                f"registered: {registry.list_codes()}"
            )

        effective_adapter = data.adapter_type or provider.adapter_type
        effective_rate_source = (
            data.rate_source if data.rate_source is not None else provider.rate_source
        )
        effective_rate_config_id = (
            data.rate_config_id
            if data.rate_config_id is not None
            else provider.rate_config_id
        )
        await self._validate_rate_config(
            adapter_type=effective_adapter,
            rate_source=effective_rate_source,
            rate_config_id=effective_rate_config_id,
        )

        if data.min_amount_fiat is not None:
            update_payload["min_amount_fiat"] = Decimal(str(data.min_amount_fiat))
        if data.max_amount_fiat is not None:
            update_payload["max_amount_fiat"] = Decimal(str(data.max_amount_fiat))
        if data.api_key is not None:
            update_payload["api_key_encrypted"] = encrypt_api_secret(data.api_key)
        if data.api_secret is not None:
            update_payload["api_secret_encrypted"] = encrypt_api_secret(data.api_secret)
        if data.webhook_secret is not None:
            update_payload["webhook_secret_encrypted"] = encrypt_api_secret(
                data.webhook_secret
            )

        if not update_payload:
            return provider

        provider = await self.providers.update(provider_id, update_payload)
        await self.audit_log(
            action="cascade_provider_update",
            entity_type="cascade_provider",
            entity_id=provider_id,
            new_values={k: v for k, v in update_payload.items() if "encrypted" not in k},
        )
        return provider

    async def delete_provider(self, provider_id: int) -> None:
        """Soft-delete by deactivating; hard-delete blocked when attempts exist.

        Hard-deleting a provider with order history would orphan financial
        ledger rows that reference its virtual user. Admin can disable a
        provider, but full removal needs a migration story we haven't built.
        """
        provider = await self.providers.get(provider_id)
        if not provider:
            raise NotFoundException(f"Cascade provider {provider_id} not found")

        existing = await self.session.execute(
            select(CascadeOrderAttempt.id)
            .where(CascadeOrderAttempt.provider_id == provider_id)
            .limit(1)
        )
        if existing.scalars().first():
            await self.providers.update(provider_id, {"is_active": False})
            await self.audit_log(
                action="cascade_provider_deactivate",
                entity_type="cascade_provider",
                entity_id=provider_id,
            )
            return

        await self.providers.delete(provider_id)
        await self.audit_log(
            action="cascade_provider_delete",
            entity_type="cascade_provider",
            entity_id=provider_id,
        )

    async def adjust_provider_balance(
        self, provider_id: int, delta_usdt: Decimal, *, reason: str
    ) -> Decimal:
        """Apply a manual delta to the provider's virtual user WORK balance.

        Used to reconcile our actual settlement with the provider — admin
        moves USDT in/out of the virtual trader's WORK balance to keep our
        local view in sync with reality.
        """
        from app.modules.finance.service import FinanceService

        provider = await self.providers.get(provider_id)
        if not provider:
            raise NotFoundException(f"Cascade provider {provider_id} not found")

        virtual_user = await self.session.get(User, provider.virtual_user_id)
        if not virtual_user:
            raise ValidationException("Virtual user missing — provider state is corrupt")

        finance = FinanceService(self.session)
        async with self.session.begin_nested():
            # Through the ledger (one-sided DEPOSIT/WITHDRAWAL) so the change is
            # traceable; a debit beyond the balance raises Insufficient funds.
            await finance.adjust_provider_balance(
                virtual_user, Decimal(str(delta_usdt)),
                reason=f"Cascade provider balance adjust: {reason}",
            )
        balance = await finance.get_or_create_user_balance(
            virtual_user, BalanceType.WORK, Currency.USDT
        )
        new_amount = Decimal(balance.amount or 0)

        await self.audit_log(
            action="cascade_provider_balance_adjust",
            entity_type="cascade_provider",
            entity_id=provider_id,
            new_values={"delta_usdt": str(delta_usdt), "reason": reason},
        )
        return new_amount

    # ------------------ group CRUD ------------------

    async def create_group(self, data: CascadeGroupCreate) -> CascadeGroup:
        existing = await self.session.execute(
            select(CascadeGroup).where(CascadeGroup.name == data.name)
        )
        if existing.scalars().first():
            raise ValidationException(f"Group {data.name!r} already exists")

        async with self.session.begin_nested():
            group = await self.groups.create(
                {
                    "name": data.name,
                    "description": data.description,
                    "tier": data.tier,
                    "timeout_ms": data.timeout_ms,
                    "is_active": data.is_active,
                }
            )
            for provider_id in data.provider_ids:
                await self.session.execute(
                    cascade_group_providers.insert().values(
                        group_id=group.id, provider_id=provider_id
                    )
                )
            for merchant_id in data.merchant_ids:
                await self.session.execute(
                    cascade_group_merchants.insert().values(
                        group_id=group.id, merchant_id=merchant_id
                    )
                )

        await self.audit_log(
            action="cascade_group_create",
            entity_type="cascade_group",
            entity_id=group.id,
            new_values={"name": group.name, "tier": group.tier},
        )
        return await self.groups.get_with_relations(group.id)

    async def update_group(
        self, group_id: int, data: CascadeGroupUpdate
    ) -> CascadeGroup:
        group = await self.groups.get(group_id)
        if not group:
            raise NotFoundException(f"Cascade group {group_id} not found")

        payload = {k: v for k, v in data.model_dump(exclude_none=True).items()}
        if not payload:
            return await self.groups.get_with_relations(group_id)

        await self.groups.update(group_id, payload)
        await self.audit_log(
            action="cascade_group_update",
            entity_type="cascade_group",
            entity_id=group_id,
            new_values=payload,
        )
        return await self.groups.get_with_relations(group_id)

    async def delete_group(self, group_id: int) -> None:
        group = await self.groups.get(group_id)
        if not group:
            raise NotFoundException(f"Cascade group {group_id} not found")
        await self.groups.delete(group_id)
        await self.audit_log(
            action="cascade_group_delete",
            entity_type="cascade_group",
            entity_id=group_id,
        )

    async def attach_provider(self, group_id: int, provider_id: int) -> None:
        await self._toggle_link(
            cascade_group_providers,
            group_id=group_id,
            field_name="provider_id",
            other_id=provider_id,
            attach=True,
        )

    async def detach_provider(self, group_id: int, provider_id: int) -> None:
        await self._toggle_link(
            cascade_group_providers,
            group_id=group_id,
            field_name="provider_id",
            other_id=provider_id,
            attach=False,
        )

    async def attach_merchant(self, group_id: int, merchant_id: int) -> None:
        await self._toggle_link(
            cascade_group_merchants,
            group_id=group_id,
            field_name="merchant_id",
            other_id=merchant_id,
            attach=True,
        )

    async def detach_merchant(self, group_id: int, merchant_id: int) -> None:
        await self._toggle_link(
            cascade_group_merchants,
            group_id=group_id,
            field_name="merchant_id",
            other_id=merchant_id,
            attach=False,
        )

    async def _toggle_link(
        self, table, *, group_id: int, field_name: str, other_id: int, attach: bool
    ) -> None:
        if attach:
            await self.session.execute(
                pg_insert(table)
                .values(group_id=group_id, **{field_name: other_id})
                .on_conflict_do_nothing()
            )
        else:
            await self.session.execute(
                table.delete().where(
                    table.c.group_id == group_id,
                    getattr(table.c, field_name) == other_id,
                )
            )

    # ------------------ cascade routing (the hot path) ------------------

    async def try_cascade(
        self, *, merchant: Merchant, order_data: Dict[str, Any], snapshot: Dict[str, Any]
    ) -> CascadeResult:
        """Main entry point — called by OrderService when local pooling fails.

        ``order_data`` is the *under-construction* dict that the caller will
        merge into the future Order row (carries amount, currency, method,
        amount_usdt, fee_usdt, etc). We never mutate it; the caller decides
        what to do with our result.

        ``snapshot`` is the diagnostic dict written into OrderCreationSnapshot —
        we append our trail under ``snapshot['cascade']`` for debug.
        """
        diagnostics: List[Dict[str, Any]] = []
        snapshot.setdefault("cascade", {"mode": merchant.cascade_mode.value, "attempts": []})
        budget_ms = max(merchant.requisite_search_timeout_ms, 500)
        deadline = time.monotonic() + budget_ms / 1000

        try:
            if merchant.cascade_mode == CascadeMode.GROUPED:
                groups = await self.groups.list_for_merchant(merchant.id)
                for group in groups:
                    remaining_ms = int((deadline - time.monotonic()) * 1000)
                    if remaining_ms <= 0:
                        diagnostics.append({"event": "global_timeout"})
                        break
                    group_budget_ms = min(group.timeout_ms, remaining_ms)
                    result = await self._race_group(
                        merchant=merchant,
                        group=group,
                        order_data=order_data,
                        budget_ms=group_budget_ms,
                        diagnostics=diagnostics,
                    )
                    if result.success:
                        snapshot["cascade"]["attempts"] = diagnostics
                        return result
            elif merchant.cascade_mode == CascadeMode.POOLED:
                providers = await rank_pool_providers(self.session)
                for provider in providers:
                    remaining_ms = int((deadline - time.monotonic()) * 1000)
                    if remaining_ms <= 0:
                        diagnostics.append({"event": "global_timeout"})
                        break
                    attempt_budget = min(provider.request_timeout_ms, remaining_ms)
                    result = await self._try_pool_one(
                        merchant=merchant,
                        provider=provider,
                        order_data=order_data,
                        budget_ms=attempt_budget,
                        diagnostics=diagnostics,
                    )
                    if result.success:
                        snapshot["cascade"]["attempts"] = diagnostics
                        return result
        finally:
            snapshot["cascade"]["attempts"] = diagnostics

        return CascadeResult(success=False, diagnostics=diagnostics)

    # ─── Admin-side: one-off live probe against a specific provider ─────

    async def test_provider_issue(
        self,
        *,
        provider_id: int,
        amount: Decimal,
        payment_method: PaymentMethod,
        payment_option_code: Optional[str],
        auto_cancel: bool = True,
    ) -> Dict[str, Any]:
        """Hit a single provider's ``issue_requisite`` with a synthesised
        order_data and report what happened — without writing anything to
        our DB (no Order, no Requisite, no Attempt).

        This is the bottom of the cascade pyramid: if **this** call works
        the whole flow can work; if it fails, you know it's not a
        scheduling/pooling issue but a real adapter/provider problem.

        Always (best-effort) cancels a successful reservation right after
        so the trader on the provider side isn't held hostage by your
        clicking around in the admin UI. Pass ``auto_cancel=False`` if
        you want to keep the reservation and run e.g. a subsequent
        ``cancel`` API call manually.

        Returns a structured dict suitable for direct JSON serialisation
        to the admin UI (no ORM objects, no datetimes — everything is a
        primitive).
        """
        provider = await self.providers.get(provider_id)
        if not provider:
            raise NotFoundException(f"Cascade provider {provider_id} not found")

        adapter = self._adapter(provider)
        # Cheap sanity check — surfaces "this adapter explicitly doesn't
        # support this method" before we even talk to the network. Matches
        # what the scheduler does at pool entry.
        if not adapter.supports(
            provider=provider,
            method=payment_method,
            payment_option_code=payment_option_code,
        ):
            return {
                "ok": False,
                "outcome": "unsupported",
                "summary": (
                    f"Adapter '{provider.adapter_type}' does not support "
                    f"method={payment_method.value} for this provider's config"
                ),
                "provider_id": provider.id,
                "provider_code": provider.code,
                "adapter_type": provider.adapter_type,
            }

        idem = f"admin-test-{uuid.uuid4().hex[:12]}"
        order_data: Dict[str, Any] = {
            "amount": amount,
            "payment_method": payment_method,
            "payment_option_code": payment_option_code,
            "merchant_request_id": idem,
            "client_user_id": idem,
            # Some adapters (Bitwire / Garex) embed a callback URL into the
            # outbound body; pass the configured one through so the request
            # shape is identical to what the scheduler would send.
            "client_full_name": "Admin Probe",
        }
        timeout_ms = provider.request_timeout_ms or 5000
        started_at = utcnow()

        # Build a "what we're about to send" preview so the admin UI can
        # show URL + headers + body even when the request fails with a
        # cryptic 400/401 on the provider side. Reuses the exact same
        # helpers the live ``issue_requisite`` does, so the preview is
        # the request — not a paraphrase of it. Wrapped in a try because
        # adapters with a custom ``issue_requisite`` (Mock) might not
        # implement ``build_payin_request``; in that case we just leave
        # the preview empty rather than failing the whole probe.
        request_preview = await self._build_request_preview(
            adapter=adapter,
            provider=provider,
            order_data=order_data,
            idempotency_key=idem,
            method=payment_method,
        )

        base_payload: Dict[str, Any] = {
            "provider_id": provider.id,
            "provider_code": provider.code,
            "adapter_type": provider.adapter_type,
            "idempotency_key": idem,
            "started_at": started_at.isoformat(),
            "request": {
                "amount": str(amount),
                "payment_method": payment_method.value,
                "payment_option_code": payment_option_code,
            },
            "request_preview": request_preview,
        }

        try:
            with provider_request_type("payin"):
                result: IssueResult = await asyncio.wait_for(
                    adapter.issue_requisite(
                        provider=provider,
                        order_data=order_data,
                        idempotency_key=idem,
                        timeout_ms=timeout_ms,
                    ),
                    timeout=timeout_ms / 1000,
                )
        except asyncio.TimeoutError:
            return {
                **base_payload,
                "ok": False,
                "outcome": "timeout",
                "summary": f"adapter timed out after {timeout_ms}ms",
                "finished_at": utcnow().isoformat(),
            }
        except Exception as exc:  # noqa: BLE001 — surface everything to admin
            return {
                **base_payload,
                "ok": False,
                "outcome": "error",
                "summary": f"{type(exc).__name__}: {exc}",
                "finished_at": utcnow().isoformat(),
            }

        finished_at = utcnow()
        base_payload["finished_at"] = finished_at.isoformat()
        base_payload["latency_ms"] = int(
            (finished_at - started_at).total_seconds() * 1000
        )

        if isinstance(result, ProviderRefusal):
            return {
                **base_payload,
                "ok": False,
                "outcome": "refusal",
                "summary": f"provider refused: {result.code}",
                "refusal_code": result.code,
                "refusal_message": result.message,
                "raw": result.raw,
            }

        # Success — we got a real reservation. Capture the requisite,
        # then immediately roll it back unless the caller asked to keep
        # it. ``cancel_request`` is best-effort; failures here don't
        # invalidate the test result, they just get reported.
        cancel_status: Optional[Dict[str, Any]] = None
        if auto_cancel:
            try:
                with provider_request_type("cancel"):
                    cancel_ok = await adapter.cancel_request(
                        provider=provider,
                        external_order_id=result.external_order_id,
                        timeout_ms=provider.cancel_timeout_ms or 2000,
                    )
                cancel_status = {"called": True, "ok": bool(cancel_ok)}
            except Exception as exc:  # noqa: BLE001
                cancel_status = {
                    "called": True,
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }

        return {
            **base_payload,
            "ok": True,
            "outcome": "issued",
            "summary": f"reserved requisite {result.external_order_id}",
            "requisite": {
                "external_order_id": result.external_order_id,
                "bank_name": result.bank_name,
                "account_number": result.account_number,
                "account_holder": result.account_holder,
                "payment_method": result.payment_method.value,
                "payment_option_code": result.payment_option_code,
                "amount_fiat": str(result.amount_fiat),
                "provider_rate": (
                    str(result.provider_rate)
                    if result.provider_rate is not None
                    else None
                ),
                "expires_at": result.expires_at.isoformat(),
            },
            "auto_cancel": cancel_status,
            "raw": result.raw,
        }

    async def _build_request_preview(
        self,
        *,
        adapter: ProviderAdapter,
        provider: CascadeProvider,
        order_data: Dict[str, Any],
        idempotency_key: str,
        method: PaymentMethod,
    ) -> Optional[Dict[str, Any]]:
        """Return a serialisable snapshot of the outgoing HTTP request.

        Reuses the exact code path of the live ``issue_requisite``:
          1. ``build_payin_request`` for method/path/body
          2. ``acquire_token`` (handles JWT cache / refresh)
          3. ``sign_request`` for the final header set

        Sensitive header values are masked (Authorization tokens,
        signatures, API keys) — leaves only a "first 6 chars … length"
        fingerprint so admins can compare what they think the request
        sends with what it actually sends, without leaking secrets into
        screenshots / Sentry breadcrumbs.

        Returns ``None`` when the adapter doesn't follow the
        Template-Method ``issue_requisite`` (e.g. Mock raises
        ``NotImplementedError`` for ``build_payin_request``). The probe
        still runs — admin just won't see a preview.
        """
        try:
            method_value = adapter.resolve_method_value(provider, method)
            if method_value is None:
                return None
            request = adapter.build_payin_request(
                provider=provider,
                order_data=order_data,
                idempotency_key=idempotency_key,
                method=method,
                method_value=method_value,
            )
        except NotImplementedError:
            return None
        except Exception as exc:  # noqa: BLE001 — never poison the probe with builder errors
            return {"error": f"{type(exc).__name__}: {exc}"}
        if isinstance(request, ProviderRefusal):
            # Adapter refused before even building the request — nothing
            # to preview; the outer call will surface the refusal anyway.
            return None
        try:
            token = await adapter.acquire_token(provider)
            headers = adapter.sign_request(
                token=token,
                method=request.http_method,
                path=request.path,
                body=request.body,
                idempotency_key=request.idempotency_key or idempotency_key,
                extra_headers=request.extra_headers,
                provider=provider,
            )
        except Exception as exc:  # noqa: BLE001
            return {"error": f"sign_request: {type(exc).__name__}: {exc}"}

        return {
            "method": request.http_method,
            "url": f"{(provider.base_url or '').rstrip('/')}{request.path}",
            "headers": _mask_sensitive_headers(headers),
            "body": request.body,
            "params": dict(request.params or {}),
        }

    # ----- internals -----

    def _adapter(self, provider: CascadeProvider) -> ProviderAdapter:
        return registry.get(provider.adapter_type)

    async def _validate_rate_config(
        self,
        *,
        adapter_type: str,
        rate_source: CascadeRateSource,
        rate_config_id: Optional[int],
    ) -> None:
        """Reject inconsistent (rate_source, rate_config_id, adapter) combinations.

        - PLATFORM mode requires rate_config_id pointing at an existing config.
        - PROVIDER mode requires the adapter to declare supports_provider_rate.
          Otherwise we'd have no rate to stamp on the order.
        """
        if rate_source == CascadeRateSource.PLATFORM:
            if not rate_config_id:
                raise ValidationException(
                    "rate_config_id is required when rate_source = 'platform'"
                )
            from app.modules.rates.models import RateConfig

            cfg = await self.session.get(RateConfig, rate_config_id)
            if not cfg:
                raise ValidationException(
                    f"RateConfig #{rate_config_id} not found"
                )
            return
        # PROVIDER mode
        if registry.has(adapter_type):
            adapter_cls = type(registry.get(adapter_type))
            if not adapter_cls.supports_provider_rate:
                raise ValidationException(
                    f"Adapter {adapter_type!r} does not quote its own rate — "
                    "configure rate_source='platform' with a rate_config_id."
                )

    def _eligible_providers(
        self,
        *,
        providers: List[CascadeProvider],
        order_data: Dict[str, Any],
        diagnostics: List[Dict[str, Any]],
    ) -> List[CascadeProvider]:
        method: PaymentMethod = order_data["payment_method"]
        if not isinstance(method, PaymentMethod):
            method = PaymentMethod(method)
        amount = Decimal(str(order_data["amount"]))
        option_code = order_data.get("payment_option_code")

        eligible: List[CascadeProvider] = []
        for provider in providers:
            if not provider.is_active:
                diagnostics.append(
                    {"provider_id": provider.id, "skip": "inactive"}
                )
                continue
            if provider.disabled_until and provider.disabled_until > utcnow():
                diagnostics.append(
                    {"provider_id": provider.id, "skip": "circuit_open"}
                )
                continue
            if (
                provider.min_amount_fiat is not None
                and amount < provider.min_amount_fiat
            ):
                diagnostics.append(
                    {"provider_id": provider.id, "skip": "amount_below_min"}
                )
                continue
            if (
                provider.max_amount_fiat is not None
                and amount > provider.max_amount_fiat
            ):
                diagnostics.append(
                    {"provider_id": provider.id, "skip": "amount_above_max"}
                )
                continue
            adapter = self._adapter(provider)
            if not adapter.supports(
                provider=provider, method=method, payment_option_code=option_code
            ):
                diagnostics.append(
                    {
                        "provider_id": provider.id,
                        "skip": "method_unsupported",
                        "method": method.value,
                    }
                )
                continue
            eligible.append(provider)
        return eligible

    async def _filter_funded(
        self,
        providers: List[CascadeProvider],
        order_data: Dict[str, Any],
        diagnostics: List[Dict[str, Any]],
    ) -> List[CascadeProvider]:
        """Drop providers whose virtual trader can't cover the escrow freeze
        that order creation will perform.

        Order creation freezes the provider's virtual trader WORK→ESCROW for
        ``amount_usdt``. If that balance is short, ``finance.create_order``
        raises and our order rolls back — but the provider would already have
        issued a real requisite (an orphaned deal on their side). So we gate the
        provider call on the virtual trader's USDT WORK balance HERE, before any
        HTTP request, and skip under-funded providers entirely.

        Runs in the sequential pre-race phase only — DB access is safe here; the
        concurrent issue coroutines must not touch the session.
        """
        raw = order_data.get("amount_usdt")
        if raw is None:
            return providers
        amount_usdt = Decimal(str(raw))
        if amount_usdt <= 0:
            return providers

        from app.modules.finance.models import Balance

        funded: List[CascadeProvider] = []
        for provider in providers:
            res = await self.session.execute(
                select(Balance.amount).where(
                    Balance.user_id == provider.virtual_user_id,
                    Balance.type == BalanceType.WORK,
                    Balance.currency == Currency.USDT,
                )
            )
            available = res.scalar_one_or_none()
            if available is not None and Decimal(str(available)) >= amount_usdt:
                funded.append(provider)
            else:
                diagnostics.append({
                    "provider_id": provider.id,
                    "skip": "insufficient_virtual_balance",
                    "needed_usdt": float(amount_usdt),
                    "available_usdt": float(available or 0),
                })
        return funded

    async def _race_group(
        self,
        *,
        merchant: Merchant,
        group: CascadeGroup,
        order_data: Dict[str, Any],
        budget_ms: int,
        diagnostics: List[Dict[str, Any]],
    ) -> CascadeResult:
        eligible = self._eligible_providers(
            providers=list(group.providers),
            order_data=order_data,
            diagnostics=diagnostics,
        )
        eligible = await self._filter_funded(eligible, order_data, diagnostics)
        if not eligible:
            diagnostics.append(
                {"group_id": group.id, "tier": group.tier, "skip": "no_eligible"}
            )
            return CascadeResult(success=False, diagnostics=diagnostics)

        # Snapshot everything we need before kicking off concurrent calls — once
        # tasks start they MUST NOT touch the SQLAlchemy session.
        adapter_calls: Dict[int, asyncio.Task] = {}
        idempotency_keys: Dict[int, str] = {p.id: uuid.uuid4().hex for p in eligible}

        for provider in eligible:
            adapter_calls[provider.id] = asyncio.create_task(
                self._call_adapter_issue(
                    provider=provider,
                    order_data=order_data,
                    idempotency_key=idempotency_keys[provider.id],
                    timeout_ms=budget_ms,
                )
            )

        winner_provider_id: Optional[int] = None
        outcomes: Dict[int, _RawAttemptOutcome] = {}
        deadline = asyncio.get_event_loop().time() + budget_ms / 1000

        pending = set(adapter_calls.values())
        try:
            while pending:
                timeout = max(0.0, deadline - asyncio.get_event_loop().time())
                done, pending = await asyncio.wait(
                    pending,
                    timeout=timeout if timeout > 0 else 0.001,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not done:
                    break
                for task in done:
                    provider_id = next(
                        pid for pid, t in adapter_calls.items() if t is task
                    )
                    outcome = self._resolve_task(provider_id, task, idempotency_keys[provider_id])
                    outcomes[provider_id] = outcome
                    if (
                        outcome.status == CascadeAttemptStatus.WON
                        and winner_provider_id is None
                    ):
                        winner_provider_id = provider_id
                if winner_provider_id is not None:
                    break
        finally:
            # Cancel still-pending coroutines (timeout reached or winner found).
            cancelled_pending = list(pending)
            for task in cancelled_pending:
                task.cancel()
            await asyncio.gather(*cancelled_pending, return_exceptions=True)
            for task in cancelled_pending:
                provider_id = next(
                    pid for pid, t in adapter_calls.items() if t is task
                )
                if provider_id not in outcomes:
                    # If a winner was found, the rest were cancelled mid-flight;
                    # otherwise the whole group ran out of time → TIMEOUT.
                    status = (
                        CascadeAttemptStatus.CANCELLED
                        if winner_provider_id is not None
                        else CascadeAttemptStatus.TIMEOUT
                    )
                    outcomes[provider_id] = _RawAttemptOutcome(
                        provider_id=provider_id,
                        started_at=utcnow(),
                        finished_at=utcnow(),
                        latency_ms=budget_ms,
                        status=status,
                        idempotency_key=idempotency_keys[provider_id],
                    )

        # Persist all attempts. Winner first so the row is available for
        # _materialize_winner before we update losers' status to LOST.
        attempt_rows: Dict[int, CascadeOrderAttempt] = {}
        for provider_id, outcome in outcomes.items():
            provider = next(p for p in eligible if p.id == provider_id)
            attempt = await self._persist_attempt(
                provider=provider,
                group=group,
                order_id=order_data.get("id"),  # may be None when called pre-create
                outcome=outcome,
            )
            attempt_rows[provider_id] = attempt
            await self._update_breaker(provider, outcome)
            diagnostics.append(
                {
                    "provider_id": provider_id,
                    "group_id": group.id,
                    "tier": group.tier,
                    "status": outcome.status.value,
                    "latency_ms": outcome.latency_ms,
                    "refusal": outcome.refusal_reason,
                    "error": outcome.error_code,
                }
            )

        if winner_provider_id is None:
            return CascadeResult(success=False, diagnostics=diagnostics)

        # Cancel the runner-ups. We send a cancel to *every* non-winner whose
        # request might have reached the provider — this covers both:
        #   1. WON-but-lost races (external_order_id is known) → mark LOST,
        #   2. CANCELLED locally before response (HTTP may have shipped) →
        #      use idempotency_key as cancel reference; the provider can
        #      no-op if nothing was issued under that key.
        # Safe-by-default: the alternative (only cancelling cases we observed
        # WON locally) leaks reservations on the provider when a slow response
        # arrives after we already cancelled the task.
        winner_outcome = outcomes[winner_provider_id]
        winner_provider = next(p for p in eligible if p.id == winner_provider_id)
        for provider_id, outcome in outcomes.items():
            if provider_id == winner_provider_id:
                continue
            if outcome.status == CascadeAttemptStatus.WON and outcome.response is not None:
                attempt_rows[provider_id].status = CascadeAttemptStatus.LOST
                self.session.add(attempt_rows[provider_id])
                provider = next(p for p in eligible if p.id == provider_id)
                self._enqueue_cancel(
                    provider_code=provider.code,
                    external_order_id=outcome.response.external_order_id,
                )
            elif outcome.status == CascadeAttemptStatus.CANCELLED:
                provider = next(p for p in eligible if p.id == provider_id)
                self._enqueue_cancel(
                    provider_code=provider.code,
                    external_order_id=outcome.idempotency_key,
                )
        await self.session.flush()

        result = await self._materialize_winner(
            provider=winner_provider,
            outcome=winner_outcome,
            order_data=order_data,
            attempt=attempt_rows[winner_provider_id],
        )
        return result

    async def _try_pool_one(
        self,
        *,
        merchant: Merchant,
        provider: CascadeProvider,
        order_data: Dict[str, Any],
        budget_ms: int,
        diagnostics: List[Dict[str, Any]],
    ) -> CascadeResult:
        eligible = self._eligible_providers(
            providers=[provider],
            order_data=order_data,
            diagnostics=diagnostics,
        )
        eligible = await self._filter_funded(eligible, order_data, diagnostics)
        if not eligible:
            return CascadeResult(success=False, diagnostics=diagnostics)

        idempotency_key = uuid.uuid4().hex
        task = asyncio.create_task(
            self._call_adapter_issue(
                provider=provider,
                order_data=order_data,
                idempotency_key=idempotency_key,
                timeout_ms=budget_ms,
            )
        )
        try:
            await asyncio.wait_for(task, timeout=budget_ms / 1000)
        except asyncio.TimeoutError:
            pass
        outcome = self._resolve_task(provider.id, task, idempotency_key)

        attempt = await self._persist_attempt(
            provider=provider,
            group=None,
            order_id=order_data.get("id"),
            outcome=outcome,
        )
        await self._update_breaker(provider, outcome)
        diagnostics.append(
            {
                "provider_id": provider.id,
                "pool": True,
                "status": outcome.status.value,
                "latency_ms": outcome.latency_ms,
                "refusal": outcome.refusal_reason,
                "error": outcome.error_code,
            }
        )

        if outcome.status != CascadeAttemptStatus.WON:
            return CascadeResult(success=False, diagnostics=diagnostics)

        return await self._materialize_winner(
            provider=provider,
            outcome=outcome,
            order_data=order_data,
            attempt=attempt,
        )

    async def _call_adapter_issue(
        self,
        *,
        provider: CascadeProvider,
        order_data: Dict[str, Any],
        idempotency_key: str,
        timeout_ms: int,
    ) -> _RawAttemptOutcome:
        """Run adapter.issue_requisite and translate the result into _RawAttemptOutcome.

        This coroutine is the unit of parallelism — it MUST NOT touch the DB.
        """
        adapter = self._adapter(provider)
        started = utcnow()
        try:
            with provider_request_type("payin"):
                result: IssueResult = await asyncio.wait_for(
                    adapter.issue_requisite(
                        provider=provider,
                        order_data=order_data,
                        idempotency_key=idempotency_key,
                        timeout_ms=timeout_ms,
                    ),
                    timeout=timeout_ms / 1000,
                )
        except asyncio.TimeoutError:
            finished = utcnow()
            return _RawAttemptOutcome(
                provider_id=provider.id,
                started_at=started,
                finished_at=finished,
                latency_ms=int((finished - started).total_seconds() * 1000),
                status=CascadeAttemptStatus.TIMEOUT,
                idempotency_key=idempotency_key,
            )
        except Exception as exc:
            finished = utcnow()
            logger.warning(
                "cascade_adapter_error",
                provider_id=provider.id,
                provider_code=provider.code,
                error=str(exc),
            )
            return _RawAttemptOutcome(
                provider_id=provider.id,
                started_at=started,
                finished_at=finished,
                latency_ms=int((finished - started).total_seconds() * 1000),
                status=CascadeAttemptStatus.ERROR,
                idempotency_key=idempotency_key,
                error_code=type(exc).__name__,
                error_message=str(exc)[:500],
            )

        finished = utcnow()
        latency_ms = int((finished - started).total_seconds() * 1000)
        if isinstance(result, ProviderRefusal):
            return _RawAttemptOutcome(
                provider_id=provider.id,
                started_at=started,
                finished_at=finished,
                latency_ms=latency_ms,
                status=CascadeAttemptStatus.REFUSED,
                idempotency_key=idempotency_key,
                refusal_reason=f"{result.code}: {result.message}",
            )
        if isinstance(result, ProviderRequisiteResponse):
            return _RawAttemptOutcome(
                provider_id=provider.id,
                started_at=started,
                finished_at=finished,
                latency_ms=latency_ms,
                status=CascadeAttemptStatus.WON,
                idempotency_key=idempotency_key,
                response=result,
            )
        # Adapter returned an unexpected shape — treat as error so the cascade
        # can fall through to the next provider/group.
        return _RawAttemptOutcome(
            provider_id=provider.id,
            started_at=started,
            finished_at=finished,
            latency_ms=latency_ms,
            status=CascadeAttemptStatus.ERROR,
            idempotency_key=idempotency_key,
            error_code="adapter_contract_violation",
            error_message=f"Unexpected return type: {type(result).__name__}",
        )

    def _resolve_task(
        self,
        provider_id: int,
        task: asyncio.Task,
        idempotency_key: str,
    ) -> _RawAttemptOutcome:
        if task.cancelled():
            return _RawAttemptOutcome(
                provider_id=provider_id,
                started_at=utcnow(),
                finished_at=utcnow(),
                latency_ms=0,
                status=CascadeAttemptStatus.CANCELLED,
                idempotency_key=idempotency_key,
            )
        try:
            return task.result()
        except asyncio.CancelledError:
            return _RawAttemptOutcome(
                provider_id=provider_id,
                started_at=utcnow(),
                finished_at=utcnow(),
                latency_ms=0,
                status=CascadeAttemptStatus.CANCELLED,
                idempotency_key=idempotency_key,
            )
        except Exception as exc:
            return _RawAttemptOutcome(
                provider_id=provider_id,
                started_at=utcnow(),
                finished_at=utcnow(),
                latency_ms=0,
                status=CascadeAttemptStatus.ERROR,
                idempotency_key=idempotency_key,
                error_code=type(exc).__name__,
                error_message=str(exc)[:500],
            )

    async def _persist_attempt(
        self,
        *,
        provider: CascadeProvider,
        group: Optional[CascadeGroup],
        order_id: Optional[int],
        outcome: _RawAttemptOutcome,
    ) -> CascadeOrderAttempt:
        snapshot = None
        external_order_id = None
        provider_rate = None
        if outcome.response:
            snapshot = {
                "bank_name": outcome.response.bank_name,
                "account_number": outcome.response.account_number,
                "account_holder": outcome.response.account_holder,
                "payment_method": outcome.response.payment_method.value,
                "payment_option_code": outcome.response.payment_option_code,
                "amount_fiat": str(outcome.response.amount_fiat),
                "expires_at": outcome.response.expires_at.isoformat(),
                "raw": outcome.response.raw,
            }
            external_order_id = outcome.response.external_order_id
            provider_rate = outcome.response.provider_rate

        attempt = await self.attempts.create(
            {
                "order_id": order_id,
                "provider_id": provider.id,
                "group_id": group.id if group else None,
                "tier": group.tier if group else None,
                "started_at": outcome.started_at,
                "finished_at": outcome.finished_at,
                "latency_ms": outcome.latency_ms,
                "status": outcome.status,
                "refusal_reason": outcome.refusal_reason,
                "error_code": outcome.error_code,
                "error_message": outcome.error_message,
                "external_order_id": external_order_id,
                "requisite_snapshot": snapshot,
                "provider_rate": provider_rate,
                "idempotency_key": outcome.idempotency_key,
            }
        )
        return attempt

    async def _update_breaker(
        self, provider: CascadeProvider, outcome: _RawAttemptOutcome
    ) -> None:
        if outcome.status in (CascadeAttemptStatus.WON, CascadeAttemptStatus.REFUSED):
            await self.breaker.record_success(provider)
        elif outcome.status in (
            CascadeAttemptStatus.TIMEOUT,
            CascadeAttemptStatus.ERROR,
        ):
            await self.breaker.record_failure(provider, code=outcome.error_code or "")

    async def _materialize_winner(
        self,
        *,
        provider: CascadeProvider,
        outcome: _RawAttemptOutcome,
        order_data: Dict[str, Any],
        attempt: CascadeOrderAttempt,
    ) -> CascadeResult:
        """Persist a one-shot Requisite from the provider's response and link
        it to the attempt. Compute provider_fee and our_profit so OrderService
        can stamp them on the Order/Ledger when it finishes building it.
        """
        assert outcome.response is not None  # guaranteed by caller (status=WON)
        amount_fiat = Decimal(str(order_data["amount"]))
        amount_usdt = Decimal(str(order_data["amount_usdt"]))

        provider_rate = await self._resolve_rate(provider, outcome.response, order_data)

        method_value = (
            order_data["payment_method"].value
            if hasattr(order_data["payment_method"], "value")
            else str(order_data["payment_method"])
        )
        provider_fee_pct = Decimal(str((provider.fees or {}).get(method_value, 0)))
        provider_fee_usdt = (amount_usdt * provider_fee_pct / Decimal("100")).quantize(
            Decimal("0.0000")
        )
        merchant_fee_usdt = Decimal(str(order_data.get("fee_usdt") or "0"))
        our_profit_usdt = (merchant_fee_usdt - provider_fee_usdt).quantize(Decimal("0.0000"))

        method = (
            outcome.response.payment_method
            if isinstance(outcome.response.payment_method, PaymentMethod)
            else PaymentMethod(outcome.response.payment_method)
        )
        currency = (
            order_data["currency"]
            if hasattr(order_data["currency"], "value")
            else Currency(order_data["currency"])
        )

        payment_option_id = order_data.get("payment_option_id")
        if payment_option_id is None and outcome.response.payment_option_code:
            from app.modules.payments.models import PaymentOption
            from sqlalchemy import select as _sa_select

            res = await self.session.execute(
                _sa_select(PaymentOption.id).where(
                    PaymentOption.code == outcome.response.payment_option_code
                )
            )
            payment_option_id = res.scalar_one_or_none()

        async with self.session.begin_nested():
            requisite = Requisite(
                trader_id=provider.virtual_user_id,
                payment_option_id=payment_option_id,
                nickname=f"cascade:{provider.code}",
                bank_name=outcome.response.bank_name,
                account_number=(
                    normalize_phone(outcome.response.account_number)
                    if is_phone_method(method)
                    else outcome.response.account_number
                ),
                account_holder=outcome.response.account_holder,
                payment_method=method,
                status=RequisiteStatus.ENABLED,
                currency=currency,
                is_active=True,
                is_archived=False,
                source=RequisiteSource.CASCADE,
            )
            self.session.add(requisite)
            await self.session.flush()
            await self.session.refresh(requisite)

            attempt.requisite_id = requisite.id
            attempt.provider_rate = provider_rate
            attempt.provider_fee_usdt = provider_fee_usdt
            attempt.our_profit_usdt = our_profit_usdt
            self.session.add(attempt)
            await self.session.flush()

        return CascadeResult(
            success=True,
            provider=provider,
            attempt=attempt,
            requisite=requisite,
            provider_fee_usdt=provider_fee_usdt,
            our_profit_usdt=our_profit_usdt,
        )

    async def _resolve_rate(
        self,
        provider: CascadeProvider,
        response: ProviderRequisiteResponse,
        order_data: Dict[str, Any],
    ) -> Decimal:
        """Pick the rate to stamp on the cascade order.

        PROVIDER mode (default): use what the adapter parsed. For adapters that
        don't fill provider_rate explicitly (e.g. LegacyCrypto via a raw rub /
        usdt response), fall back to amount_fiat / amount_usdt. Last resort is
        our own exchange_rate so we never crash on missing data.

        PLATFORM mode: look up the configured RateConfig.current_rate.
        """
        if provider.rate_source == CascadeRateSource.PLATFORM:
            from app.modules.rates.models import RateConfig

            if provider.rate_config_id:
                cfg = await self.session.get(RateConfig, provider.rate_config_id)
                if cfg and cfg.current_rate:
                    return Decimal(str(cfg.current_rate))
            # Misconfigured PROVIDER + missing config — fall through to fallback below.

        if response.provider_rate is not None:
            return Decimal(str(response.provider_rate))

        amount_fiat = Decimal(str(order_data["amount"]))
        amount_usdt = Decimal(str(order_data["amount_usdt"]))
        if amount_usdt > 0:
            return (amount_fiat / amount_usdt).quantize(Decimal("0.0001"))

        return Decimal(str(order_data.get("exchange_rate") or "1"))

    def _enqueue_cancel(self, *, provider_code: str, external_order_id: str) -> None:
        """Schedule an async cancel through Celery — fire-and-forget."""
        try:
            from app.workers.celery_app import celery_app

            celery_app.send_task(
                "app.workers.tasks.cascade.cancel_provider_request",
                args=[provider_code, external_order_id],
            )
        except Exception as exc:
            logger.warning("cascade_enqueue_cancel_failed", error=str(exc))

    # ------------------ callbacks ------------------

    async def parse_provider_callback(
        self,
        *,
        provider_code: str,
        headers: Dict[str, str],
        body: bytes,
    ) -> Tuple[CascadeProvider, ParsedCallback]:
        provider = await self.providers.get_by_code(provider_code)
        # Do NOT gate on is_active here. Deactivating a provider must stop
        # NEW deals (selection is gated in _eligible_providers / list_active),
        # but deals already routed to it MUST still settle — otherwise the
        # provider reports the outcome (e.g. ACCEPTED) and our order is stuck
        # forever while the merchant goes unpaid. This is safe: the adapter
        # still verifies the signature below, and apply_callback only mutates
        # an order that has a matching CascadeOrderAttempt — a decommissioned
        # provider with no in-flight attempts can't touch any order.
        if not provider:
            raise NotFoundException(f"Cascade provider {provider_code!r} not found")
        adapter = self._adapter(provider)
        try:
            parsed = adapter.parse_callback(
                provider=provider, headers=headers, body=body
            )
        except CallbackVerificationError as exc:
            raise UnauthorizedException(str(exc))
        return provider, parsed

    async def apply_callback(
        self,
        *,
        provider: CascadeProvider,
        parsed: ParsedCallback,
    ) -> Optional[Order]:
        """Translate a provider callback into an order state transition.

        Returns the affected Order (or None if the callback referenced an
        unknown attempt — e.g. replay after we already processed the same
        external_order_id).
        """
        attempt = await self.attempts.get_by_external(
            provider.id, parsed.external_order_id
        )
        if not attempt or not attempt.order_id:
            logger.info(
                "cascade_callback_unknown_attempt",
                provider_id=provider.id,
                external_order_id=parsed.external_order_id,
            )
            return None

        # De-dup: if the same callback already transitioned the order into a
        # terminal state, just acknowledge and move on.
        order = await self.session.get(Order, attempt.order_id)
        if not order:
            return None

        adapter = self._adapter(provider)
        target_status = adapter.map_status_to_order_status(parsed.status)

        from app.modules.orders.service import OrderService

        order_service = OrderService(self.session)
        virtual_user = await self.session.get(User, provider.virtual_user_id)

        # We act on EXACTLY ONE provider-driven transition: a terminal SUCCESS,
        # which settles the order. Everything else the provider reports
        # (failed / canceled / expired / "customer paid" / intermediate) is
        # acknowledged but NOT applied — cancellation and expiry are OURS to
        # decide via the TTL timer (``expire_orders_task``). Trusting the
        # provider's non-success statuses let it release a still-valid order's
        # escrow (or race our timer / a concurrent success callback), which
        # could empty the virtual-trader ESCROW and make the genuine success
        # fail with "Insufficient funds". Success-only closes that off.
        if target_status == OrderStatus.SUCCESS:
            if order.status in (
                OrderStatus.PENDING,
                OrderStatus.RECEIPT_UPLOADED,
                OrderStatus.DISPUTED,
            ):
                await order_service.complete_order(virtual_user, order.id)
        else:
            logger.info(
                "cascade_callback_non_success_ignored",
                provider_id=provider.id,
                order_id=order.id,
                order_status=order.status.value,
                provider_status=parsed.status.value,
                target_status=target_status.value,
            )

        return order

    # ------------------ helpers used by Celery tasks ------------------

    @staticmethod
    def is_cascade_order(order: Order) -> bool:
        if not order.requisite:
            return False
        return order.requisite.source == RequisiteSource.CASCADE

    async def get_active_attempt_for_order(
        self, order_id: int
    ) -> Optional[CascadeOrderAttempt]:
        return await self.attempts.get_won_for_order(order_id)
