import hashlib
import hmac
import json

from fastapi import Depends, Request, Security
from fastapi.security.api_key import APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenException, UnauthorizedException
from app.core.security import decrypt_api_secret
from app.infrastructure.db.session import get_db
from app.modules.merchants.auth_cache import CachedMerchant, merchant_auth_cache
from app.modules.merchants.permissions import is_merchant_active

# We expect the API key to be passed in the X-Api-Key header
api_key_header = APIKeyHeader(name="X-Api-Key", auto_error=False, scheme_name="API Key")
signature_header = APIKeyHeader(name="X-Signature", auto_error=False, scheme_name="Signature")


async def get_current_merchant(
    request: Request,
    api_key: str = Security(api_key_header),
    signature: str = Security(signature_header),
    session: AsyncSession = Depends(get_db),
) -> CachedMerchant:
    """
    Dependency to authenticate a merchant via API Key.
    Optionally validates the request signature if X-Signature header is provided.
    """
    if not api_key:
        raise UnauthorizedException("Missing X-Api-Key header")

    merchant = await merchant_auth_cache.get(api_key, session)
    if not merchant:
        raise UnauthorizedException("Invalid API Key")

    request.state.merchant_id = merchant.id

    if signature:
        # Read the raw body for signature validation
        body = await request.body()

        try:
            # Parse JSON to ensure consistent formatting for signature
            payload = json.loads(body.decode("utf-8")) if body else {}
            payload_str = json.dumps(payload, separators=(",", ":"), sort_keys=True)

            # Decrypt the secret before using it for HMAC
            plain_secret = decrypt_api_secret(merchant.api_secret)

            expected_signature = hmac.new(
                plain_secret.encode("utf-8"),
                payload_str.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()

            if not hmac.compare_digest(expected_signature, signature):
                raise UnauthorizedException("Invalid signature")

        except json.JSONDecodeError:
            raise UnauthorizedException("Invalid JSON body for signature validation")

    return merchant


async def get_active_merchant(
    merchant: CachedMerchant = Depends(get_current_merchant),
) -> CachedMerchant:
    """Authenticate the merchant AND require an *active* status.

    Use this on write / creation endpoints (e.g. creating a payin order) so a
    disabled / blocked / pending / archived merchant cannot create new orders.
    Read endpoints keep using ``get_current_merchant`` — a disabled merchant can
    still inspect its existing data. Rejection is 403 (auth succeeded, action
    forbidden). The status comes straight from the auth cache (no extra query),
    and is invalidated on admin status changes, so a ban takes effect at once
    """
    if not is_merchant_active(merchant.status):
        raise ForbiddenException(
            f"Merchant is not active (status: {merchant.status.value}); "
            "creating orders is disabled."
        )
    return merchant
