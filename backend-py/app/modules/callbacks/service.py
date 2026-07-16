import hashlib
import hmac
import json
from typing import Any, Dict, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException, NotFoundException, CallbackRetryException
from app.core.logging import get_logger
from app.modules.base.service import BaseService
from app.modules.callbacks.repository import CallbackAttemptRepository
from app.modules.merchants.models import Merchant
from app.modules.orders.models import Order

logger = get_logger(__name__)


class CallbackService(BaseService):
    def __init__(self, session: AsyncSession):
        super().__init__(session)
        self.attempt_repo = CallbackAttemptRepository(session)

    async def _get_order_with_merchant(self, order_id: int) -> tuple[Order, Merchant]:
        """Fetch order and its associated merchant."""
        from sqlalchemy.orm import selectinload
        order_result = await self.session.execute(
            select(Order)
            .options(selectinload(Order.requisite), selectinload(Order.payment_option))
            .where(Order.id == order_id)
        )
        order = order_result.scalars().first()
        if not order:
            raise NotFoundException(f"Order {order_id} not found")

        merchant_result = await self.session.execute(
            select(Merchant).where(Merchant.id == order.merchant_id)
        )
        merchant = merchant_result.scalars().first()
        if not merchant:
            raise NotFoundException(f"Merchant {order.merchant_id} not found")

        return order, merchant

    async def _get_order_dispute(self, order_id: int):
        """The dispute opened for this order (1:1), or None. Used to enrich the
        callback payload with the dispute reason / status / outcome."""
        from app.modules.disputes.models import Dispute
        result = await self.session.execute(
            select(Dispute).where(Dispute.order_id == order_id)
        )
        return result.scalars().first()

    @staticmethod
    def _canonical_body(payload: Dict[str, Any]) -> bytes:
        """Single source of truth for the on-wire callback body"""
        return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")

    @staticmethod
    def _sign_body(body: bytes, secret: str) -> str:
        """HMAC-SHA256(secret, body) as lowercase hex."""
        return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()

    def _generate_signature(self, payload: Dict[str, Any], secret: str) -> str:
        """Backward-compatible wrapper. Prefer ``_canonical_body`` + ``_sign_body``
        in new code so the signed bytes are guaranteed to equal the HTTP body."""
        return self._sign_body(self._canonical_body(payload), secret)

    def _build_payload(self, order: Order, merchant: Merchant, dispute=None) -> Dict[str, Any]:
        """Build the callback payload with current order information.

        When the order has a dispute (1:1) a ``dispute`` block is added so the
        merchant sees its reason / status / outcome; otherwise the key is omitted
        (backward-compatible).
        """
        payload: Dict[str, Any] = {
            "id": str(order.uuid),
            "internalId": order.external_id,
            "userId": order.client_user_id,
            "merchant_name": merchant.name,
            "amount": float(order.amount),
            "amount_usdt": float(order.amount_usdt) if order.amount_usdt is not None else None,
            "fee_usdt": float(order.fee_usdt) if order.fee_usdt is not None else None,
            "exchange_rate": float(order.exchange_rate) if order.exchange_rate is not None else None,
            "currency": order.currency.value,
            "status": order.status.value,
            "payment_url": order.payment_url,
            "created_at": order.created_at.isoformat() if order.created_at else None,
            "expires_at": order.date_end.isoformat() if order.date_end else None,
            "requisite": None,
        }

        requisite = order.requisite
        if requisite:
            option = order.payment_option
            bank_name = (option.name if option else None) or requisite.bank_name
            payload["requisite"] = {
                "bank_name": bank_name,
                "account_number": requisite.account_number,
                "account_holder": requisite.account_holder,
                "payment_method": requisite.payment_method.value,
                "currency": requisite.currency.value,
                "payment_option_code": option.code if option else None,
                "payment_option_name": option.name if option else None,
            }

        if dispute is not None:
            # Machine-actionable fields only: id (to correlate), status (verdict),
            # reason, and substatus. ``substatus`` is the pending proof request
            # while the dispute is open — ``null`` normally, or ``pdf_requested`` /
            # ``video_requested`` when we need the merchant to attach a PDF check
            # or a video. The free-text resolution stays human-facing — it's
            # available via the merchant API (`GET .../disputes/{uuid}`).
            payload["dispute"] = {
                "id": str(dispute.uuid),
                "status": dispute.status.value,
                "reason": dispute.reason.value,
                "substatus": dispute.substatus.value if dispute.substatus else None,
            }

        return payload

    async def send_callback(self, order_id: int, attempt_number: int = 1) -> bool:
        """
        Send a webhook callback for an order.
        Raises an exception if the request fails, allowing Celery to retry.
        """
        order, merchant = await self._get_order_with_merchant(order_id)

        # Determine target URL: Order webhook_url takes precedence over Merchant webhook_url
        target_url = order.webhook_url or merchant.webhook_url
        if not target_url:
            # WARNING level so this shows up in `docker logs worker` filtered
            # output — silent skips were the second-most-common reason the
            # admin Callbacks page looked empty.
            logger.warning(
                "No webhook URL configured for order or merchant. Skipping callback.",
                order_id=order.id,
                merchant_id=merchant.id,
            )
            return True

        dispute = await self._get_order_dispute(order.id)
        payload = self._build_payload(order, merchant, dispute=dispute)
        # Canonical serialisation: the signed bytes MUST equal the bytes we
        # put on the wire. ``httpx.post(json=payload)`` would diverge — it
        # adds spaces and doesn't sort keys — so we serialise once here and
        # pass the same bytes both into HMAC and into ``content=``.
        body = self._canonical_body(payload)
        from app.core.security import decrypt_api_secret
        plain_secret = decrypt_api_secret(merchant.api_secret)
        signature = self._sign_body(body, plain_secret)

        headers = {
            "Content-Type": "application/json",
            "X-Signature": signature,
        }

        logger.info(
            "Sending callback",
            order_id=order.id,
            url=target_url,
            attempt=attempt_number,
        )

        # SSRF guard: webhook URL is merchant-controlled. Reject non-http(s) or
        # private/loopback/link-local/metadata targets and pin DNS so the worker
        # can't be pointed at internal hosts. A blocked URL is a permanent
        # failure — record it (visible on the admin Callbacks page) and do NOT
        # retry (it will never become valid).
        from app.core.ssrf import PublicOnlyTransport, SsrfError, assert_public_url
        try:
            assert_public_url(target_url)
        except SsrfError as ssrf_err:
            logger.warning(
                "Callback URL blocked by SSRF guard",
                order_id=order.id, url=target_url, reason=str(ssrf_err),
            )
            await self.attempt_repo.create({
                "order_id": order.id,
                "url": target_url,
                "request_payload": payload,
                "response_status": None,
                "response_body": f"blocked by SSRF guard: {ssrf_err}",
                "is_successful": False,
                "attempt_number": attempt_number,
            })
            await self.session.commit()
            return False

        response_status = None
        response_body = None
        is_successful = False

        try:
            async with httpx.AsyncClient(
                timeout=10.0, follow_redirects=False, transport=PublicOnlyTransport()
            ) as client:
                response = await client.post(target_url, content=body, headers=headers)
                
            response_status = response.status_code
            response_body = response.text[:2000]  # Store up to 2000 chars of response
            
            # Consider 2xx status codes as successful
            is_successful = 200 <= response_status < 300

        except SsrfError as ssrf_err:
            # Connect-time block: PublicOnlyTransport re-validates the resolved IP
            # and raises SsrfError (NOT an httpx.RequestError) on a DNS rebind
            # between the pre-check and the connect. Permanent block — record it
            # and return without retry, consistent with the pre-check.
            logger.warning(
                "Callback URL blocked by SSRF guard at connect time",
                order_id=order.id, url=target_url, reason=str(ssrf_err),
            )
            await self.attempt_repo.create({
                "order_id": order.id,
                "url": target_url,
                "request_payload": payload,
                "response_status": None,
                "response_body": f"blocked by SSRF guard: {ssrf_err}",
                "is_successful": False,
                "attempt_number": attempt_number,
            })
            await self.session.commit()
            return False
        except httpx.RequestError as e:
            logger.warning("Callback request failed", error=str(e), order_id=order.id)
            response_body = str(e)
            is_successful = False

        # Persist the attempt before raising on failure so the row is visible
        # in the admin "Callbacks" page even when the callback failed.
        await self.attempt_repo.create(
            {
                "order_id": order.id,
                "url": target_url,
                "request_payload": payload,
                "response_status": response_status,
                "response_body": response_body,
                "is_successful": is_successful,
                "attempt_number": attempt_number,
            }
        )
        await self.session.commit()

        if not is_successful:
            # Raise a specific retry exception so the Celery task knows it failed and can retry.
            # We don't use AppException here because this is a background worker error, not an API error.
            raise CallbackRetryException(f"Callback failed with status {response_status}")

        return True
