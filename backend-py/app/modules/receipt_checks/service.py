"""Receipt-check orchestration.

Responsibilities:
  * resolve the currently active provider (admin keeps only one active);
  * dedupe by file SHA-256: a second manual click on an already-checked
    file replays the previous verdict without re-charging the trader;
  * charge the trader's USDT WORK balance via the FinanceService primitive
    (ledger entry, double-entry, RECEIPT_CHECK reference type);
  * refund automatically on provider 5xx / timeout / quota-exhausted —
    the provider explicitly told us they did not bill;
  * audit: every attempt lands in `receipt_checks` regardless of outcome.

The service is intentionally synchronous from the caller's perspective —
TREXO is a sync API, response in <90s. Auto-checks run in a Celery task
because we don't want to block the merchant's upload request, but the
service method itself is the same.
"""
from __future__ import annotations

import hashlib
import logging
import os
from decimal import Decimal
from typing import Optional, TYPE_CHECKING

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums.balances import BalanceType, LedgerReferenceType
from app.common.enums.finances import Currency
from app.common.enums.receipt_checks import (
    ReceiptCheckProviderAdapter,
    ReceiptCheckStatus,
    ReceiptCheckTrigger,
)
from app.common.enums.users import UserRole
from app.common.types import utcnow
from app.core.exceptions import (
    ConflictException,
    NotFoundException,
    ValidationException,
)
from app.core.security import encrypt_api_secret
from app.modules.base.service import BaseService
from app.modules.finance.service import FinanceService
from app.modules.orders.models import Order
from app.modules.receipt_checks.models import ReceiptCheck, ReceiptCheckProvider
from app.modules.receipt_checks.providers import build_client_for_provider
from app.modules.receipt_checks.repository import (
    ReceiptCheckProviderRepository,
    ReceiptCheckRepository,
)
from app.modules.receipt_checks.schemas import (
    ProviderCheckResult,
    ProviderCreate,
    ProviderUpdate,
)
from app.modules.users.models import User

if TYPE_CHECKING:
    from app.modules.traders.models import Trader

logger = logging.getLogger(__name__)


def _sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 64), b""):
            h.update(chunk)
    return h.hexdigest()


def _mask_api_key(plain: str) -> str:
    """`sk_live_xxxxxxxx_yyyy` → `sk_live_***yyyy`. Conservative on length so
    we never leak more than the last 4 chars."""
    if not plain:
        return ""
    tail = plain[-4:] if len(plain) > 8 else "****"
    return f"sk_live_***{tail}"


class ReceiptCheckService(BaseService):
    def __init__(self, session: AsyncSession):
        super().__init__(session)
        self.providers = ReceiptCheckProviderRepository(session)
        self.checks = ReceiptCheckRepository(session)

    # ── Admin: provider management ────────────────────────────────────

    async def list_providers(self) -> list[ReceiptCheckProvider]:
        return await self.providers.list_all()

    async def list_active_providers(self) -> list[ReceiptCheckProvider]:
        """Active providers a trader may choose from — thin pass-through so the
        API layer reads them through the service, never the repository."""
        return await self.providers.list_active()

    async def get_active_provider(self) -> Optional[ReceiptCheckProvider]:
        return await self.providers.get_active()

    async def create_provider(self, data: ProviderCreate, admin_user_id: int) -> ReceiptCheckProvider:
        existing = await self.providers.get_by_code(data.code)
        if existing:
            raise ConflictException(f"Provider with code '{data.code}' already exists")

        payload = {
            "code": data.code,
            "name": data.name,
            "adapter_type": data.adapter_type.value,
            "base_url": data.base_url,
            "api_key_encrypted": encrypt_api_secret(data.api_key),
            "api_key_tail": data.api_key[-4:] if len(data.api_key) >= 4 else None,
            "price_usdt": data.price_usdt,
            "request_timeout_ms": data.request_timeout_ms,
            "settings": data.settings or {},
            "is_active": data.is_active,
        }

        async with self.session.begin_nested():
            # Multi-active: several providers may be active at once — each active
            # provider is a selectable option for traders. We no longer deactivate
            # the others when a new one is activated.
            provider = await self.providers.create(payload)

            await self.audit_log(
                action="create_receipt_check_provider",
                entity_type="receipt_check_provider",
                entity_id=provider.id,
                user_id=admin_user_id,
                new_values={"code": provider.code, "is_active": provider.is_active},
            )
        return provider

    async def update_provider(
        self, provider_id: int, data: ProviderUpdate, admin_user_id: int
    ) -> ReceiptCheckProvider:
        provider = await self.providers.get(provider_id)
        if not provider:
            raise NotFoundException(f"Provider {provider_id} not found")

        update: dict = {}
        if data.name is not None:
            update["name"] = data.name
        if data.base_url is not None:
            update["base_url"] = data.base_url
        if data.price_usdt is not None:
            update["price_usdt"] = data.price_usdt
        if data.request_timeout_ms is not None:
            update["request_timeout_ms"] = data.request_timeout_ms
        if data.settings is not None:
            update["settings"] = data.settings
        if data.api_key is not None:
            update["api_key_encrypted"] = encrypt_api_secret(data.api_key)
            update["api_key_tail"] = data.api_key[-4:] if len(data.api_key) >= 4 else None

        async with self.session.begin_nested():
            # Multi-active: activating a provider does NOT deactivate the others.
            if data.is_active is not None:
                update["is_active"] = data.is_active

            if update:
                provider = await self.providers.update(provider_id, update)

            await self.audit_log(
                action="update_receipt_check_provider",
                entity_type="receipt_check_provider",
                entity_id=provider_id,
                user_id=admin_user_id,
                new_values={k: ("***" if "key" in k else v) for k, v in update.items()},
            )
        return provider

    async def delete_provider(self, provider_id: int, admin_user_id: int) -> None:
        provider = await self.providers.get(provider_id)
        if not provider:
            raise NotFoundException(f"Provider {provider_id} not found")
        await self.providers.delete(provider_id)
        await self.audit_log(
            action="delete_receipt_check_provider",
            entity_type="receipt_check_provider",
            entity_id=provider_id,
            user_id=admin_user_id,
        )

    async def fetch_balance(self, provider_id: int) -> dict:
        provider = await self.providers.get(provider_id)
        if not provider:
            raise NotFoundException(f"Provider {provider_id} not found")
        client = build_client_for_provider(provider)
        return await client.get_balance()

    @staticmethod
    def mask_provider(provider: ReceiptCheckProvider) -> dict:
        tail = provider.api_key_tail or ""
        return {
            "id": provider.id,
            "code": provider.code,
            "name": provider.name,
            "adapter_type": provider.adapter_type,
            "is_active": provider.is_active,
            "base_url": provider.base_url,
            "api_key_masked": f"sk_live_***{tail}" if tail else None,
            "price_usdt": provider.price_usdt,
            "request_timeout_ms": provider.request_timeout_ms,
            "settings": provider.settings or {},
            "created_at": provider.created_at,
            "updated_at": provider.updated_at,
        }

    # ── Trader / auto: run a check ────────────────────────────────────

    async def resolve_provider_for_trader(
        self,
        trader: Optional["Trader"],
        requested_id: Optional[int] = None,
    ) -> Optional[ReceiptCheckProvider]:
        """Pick the receipt-check provider for a trader's check.

        * ``requested_id`` given → it MUST be an active provider (traders may
          only use providers the admin marked active); otherwise reject. This
          is the security boundary — an inactive provider is never usable via
          the API, not just hidden in the UI.
        * else → the trader's saved default, if it still exists and is active;
        * else → the first active provider (lowest id);
        * else → ``None`` (no provider available — caller decides how to
          surface it).
        """
        if requested_id is not None:
            provider = await self.providers.get(requested_id)
            if provider is None or not provider.is_active:
                raise ValidationException(
                    "Selected receipt-check provider is not available"
                )
            return provider

        default_id = getattr(trader, "default_receipt_check_provider_id", None)
        if default_id is not None:
            provider = await self.providers.get(default_id)
            if provider is not None and provider.is_active:
                return provider

        actives = await self.providers.list_active()
        return actives[0] if actives else None

    async def run_check_from_trader_group(
        self,
        *,
        order_uuid: str,
        telegram_group_id: int,
        provider_id: Optional[int] = None,
    ) -> ReceiptCheck:
        """Trader-bot entry point: the trader taps «Проверить чек» under a receipt
        in their Telegram group and picks a provider. Authorise the order via the
        group (same gate as confirm / request-proof), resolve that trader, then run
        the MANUAL check charging them — mirrors the cabinet
        ``POST /orders/{id}/receipt-check`` flow, just group-authorised."""
        from app.modules.orders.service import OrderService
        from app.modules.traders.service import TraderService

        order = await OrderService(self.session).get_order_for_trader_group(
            order_uuid, telegram_group_id
        )
        trader_user = await self.session.get(User, order.trader_id)
        if trader_user is None:
            raise NotFoundException("Trader not found")
        trader = await TraderService(self.session).get_or_create_trader(trader_user.id)
        provider = await self.resolve_provider_for_trader(trader, provider_id)
        if provider is None:
            raise ConflictException("No active receipt-check provider is configured")
        return await self.run_check_for_order(
            order=order,
            trader_user=trader_user,
            trigger=ReceiptCheckTrigger.MANUAL,
            provider=provider,
        )

    async def run_check_for_order(
        self,
        order: Order,
        trader_user: Optional[User],
        trigger: ReceiptCheckTrigger,
        provider: Optional[ReceiptCheckProvider] = None,
    ) -> ReceiptCheck:
        """Main entry point. Caller resolves the order, the trader (if any) and
        the provider to use (via ``resolve_provider_for_trader``) and passes
        them in. We:

          1. Validate prerequisites (file present, provider available).
          2. Dedupe: if a SUCCESS/CACHED row exists for the same file →
             replay as CACHED, no charge.
          3. Otherwise: charge the trader's WORK balance, call the
             provider, store the result, refund if refundable.

        ``provider`` is optional for backward compatibility: when omitted we
        fall back to the global active provider. Real callers always pass the
        provider the trader selected.
        """
        if not order.receipt_file:
            raise ValidationException("Order has no receipt to verify")
        if not os.path.isfile(order.receipt_file):
            raise ValidationException("Receipt file is missing on disk")

        if provider is None:
            # Defensive default for callers that don't pre-resolve a provider
            # (real callers always pass one). Route through the same selector
            # as the trader path so both agree on the "first active" provider.
            provider = await self.resolve_provider_for_trader(None)
        if not provider:
            raise ConflictException(
                "No active receipt-check provider is configured. Ask the platform admin to enable one."
            )

        file_sha = _sha256_of(order.receipt_file)

        # Dedupe — never re-charge for the same file on the same order. Record
        # the replay as a zero-charge CACHED row. The migration-054 partial
        # unique index over LIVE checks (pending/success/cached) means this
        # INSERT collides with the already-present reusable (success/cached) row
        # on Postgres, so guard it exactly like the PENDING claim below and
        # replay the existing winner instead of surfacing a 500. Money-neutral
        # either way (price 0, charged False).
        reusable = await self.checks.find_reusable_for_file(order.id, file_sha)
        if reusable is not None:
            try:
                async with self.session.begin_nested():
                    cached = await self.checks.create(
                        {
                            "order_id": order.id,
                            "provider_id": provider.id,
                            "trader_user_id": trader_user.id if trader_user else None,
                            "trigger": trigger.value,
                            "status": ReceiptCheckStatus.CACHED,
                            "file_path": order.receipt_file,
                            "file_sha256": file_sha,
                            "is_clean": reusable.is_clean,
                            "verdict": reusable.verdict,
                            "parsed_data": reusable.parsed_data,
                            "provider_check_id": reusable.provider_check_id,
                            "provider_tx_id": reusable.provider_tx_id,
                            "price_usdt": Decimal("0"),
                            "charged": False,
                            "refunded": False,
                            "finished_at": utcnow(),
                        }
                    )
                return cached
            except IntegrityError:
                existing = await self.checks.find_active_for_file(order.id, file_sha)
                if existing is not None:
                    return existing
                raise

        # PDF-only enforcement matches TREXO contract; cheaper to reject
        # here than to burn a request.
        ext = os.path.splitext(order.receipt_file)[1].lower()
        if ext != ".pdf":
            row = await self.checks.create(
                {
                    "order_id": order.id,
                    "provider_id": provider.id,
                    "trader_user_id": trader_user.id if trader_user else None,
                    "trigger": trigger.value,
                    "status": ReceiptCheckStatus.FAILED,
                    "file_path": order.receipt_file,
                    "file_sha256": file_sha,
                    "price_usdt": Decimal("0"),
                    "charged": False,
                    "refunded": False,
                    "error_code": "unsupported_format",
                    "error_message": "К проверке доступны только PDF файлы",
                    "finished_at": utcnow(),
                }
            )
            return row

        price = Decimal(provider.price_usdt or 0)
        finance = FinanceService(self.session)

        # Atomic claim of (order_id, file_sha256): a partial unique index over
        # LIVE checks makes a concurrent second run (manual + auto trigger,
        # double-click, retry) conflict on this INSERT — we then replay the
        # winner instead of charging the trader twice. The charge happens AFTER
        # this row exists, so a lost claim never charges. (The dedup read above
        # only catches already-FINISHED checks; this closes the in-flight race.)
        try:
            async with self.session.begin_nested():
                check = await self.checks.create(
                    {
                        "order_id": order.id,
                        "provider_id": provider.id,
                        "trader_user_id": trader_user.id if trader_user else None,
                        "trigger": trigger.value,
                        "status": ReceiptCheckStatus.PENDING,
                        "file_path": order.receipt_file,
                        "file_sha256": file_sha,
                        "price_usdt": price,
                        "charged": False,
                        "refunded": False,
                    }
                )
        except IntegrityError:
            existing = await self.checks.find_active_for_file(order.id, file_sha)
            if existing is not None:
                return existing
            raise

        if trader_user is not None and price > 0:
            await self._charge_trader(finance, trader_user, price, check.id)
            check.charged = True
            await self.session.flush()

        client = build_client_for_provider(provider)
        try:
            result: ProviderCheckResult = await client.check_file(order.receipt_file)
        except Exception as exc:  # provider adapters shouldn't raise, but defend
            logger.exception("Provider check raised unexpectedly: %s", exc)
            result = ProviderCheckResult(
                refundable=True,
                error_code="adapter_exception",
                error_message=str(exc),
            )

        # Persist outcome + refund if applicable
        if result.error_code:
            check.status = ReceiptCheckStatus.FAILED
            check.error_code = result.error_code
            check.error_message = result.error_message
            check.raw_response = result.raw_response if isinstance(result.raw_response, dict) else None

            if result.refundable and check.charged and not check.refunded and trader_user is not None and price > 0:
                await self._refund_trader(finance, trader_user, price, check.id)
                check.refunded = True
        else:
            check.status = ReceiptCheckStatus.SUCCESS
            check.is_clean = result.is_clean
            check.verdict = [v.model_dump() for v in result.verdict] if result.verdict else []
            check.parsed_data = result.parsed_data or {}
            check.provider_check_id = result.provider_check_id
            check.provider_tx_id = result.provider_tx_id
            check.raw_response = result.raw_response if isinstance(result.raw_response, dict) else None

        check.finished_at = utcnow()
        await self.session.flush()

        await self.audit_log(
            action="run_receipt_check",
            entity_type="receipt_check",
            entity_id=check.id,
            user_id=trader_user.id if trader_user else None,
            new_values={
                "order_id": order.id,
                "trigger": trigger.value,
                "status": check.status.value if hasattr(check.status, "value") else str(check.status),
                "is_clean": check.is_clean,
                "charged": check.charged,
                "refunded": check.refunded,
            },
        )
        return check

    async def list_for_order(self, order_id: int) -> list[ReceiptCheck]:
        return await self.checks.list_for_order(order_id)

    async def latest_for_order(self, order_id: int) -> Optional[ReceiptCheck]:
        return await self.checks.latest_for_order(order_id)

    async def latest_for_orders(self, order_ids: list[int]) -> dict[int, ReceiptCheck]:
        return await self.checks.latest_for_orders(order_ids)

    # ── Internal helpers ──────────────────────────────────────────────

    async def _charge_trader(
        self, finance: FinanceService, trader_user: User, amount: Decimal, check_id: int
    ) -> None:
        """Thin wrapper — the fee money move is owned by FinanceService."""
        await finance.charge_receipt_check_fee(trader_user, amount, check_id)

    async def _refund_trader(
        self, finance: FinanceService, trader_user: User, amount: Decimal, check_id: int
    ) -> None:
        """Thin wrapper — the refund money move is owned by FinanceService."""
        await finance.refund_receipt_check_fee(trader_user, amount, check_id)
