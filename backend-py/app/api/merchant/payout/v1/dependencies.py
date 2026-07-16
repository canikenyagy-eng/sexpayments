"""Auth for the payout-terminal API (own key, separate from the payin merchant).

Mirrors the payin merchant auth: ``X-Api-Key`` identifies the terminal; an
optional ``X-Signature`` HMACs the canonical JSON body with the terminal's
secret. ``get_active_payout_terminal`` additionally requires an ENABLED terminal
(used on create).
"""
import hashlib
import hmac
import json

from fastapi import Depends, Request, Security
from fastapi.security.api_key import APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenException, UnauthorizedException
from app.core.security import decrypt_api_secret
from app.infrastructure.db.session import get_db
from app.modules.payouts.models import PayoutTerminal
from app.modules.payouts.terminal_service import PayoutTerminalService

api_key_header = APIKeyHeader(name="X-Api-Key", auto_error=False, scheme_name="Payout API Key")
signature_header = APIKeyHeader(name="X-Signature", auto_error=False, scheme_name="Payout Signature")


async def get_payout_terminal(
    request: Request,
    api_key: str = Security(api_key_header),
    signature: str = Security(signature_header),
    session: AsyncSession = Depends(get_db),
) -> PayoutTerminal:
    if not api_key:
        raise UnauthorizedException("Missing X-Api-Key header")
    terminal = await PayoutTerminalService(session).get_by_api_key(api_key)
    if not terminal:
        raise UnauthorizedException("Invalid API Key")

    if signature:
        body = await request.body()
        try:
            payload = json.loads(body.decode("utf-8")) if body else {}
        except json.JSONDecodeError:
            raise UnauthorizedException("Invalid JSON body for signature validation")
        payload_str = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        try:
            plain_secret = decrypt_api_secret(terminal.api_secret)
        except Exception:
            # Corrupted / unrotated stored secret → reject as unauthorized, not 500.
            raise UnauthorizedException("Invalid API credentials")
        expected = hmac.new(
            plain_secret.encode("utf-8"), payload_str.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise UnauthorizedException("Invalid signature")

    return terminal


async def get_active_payout_terminal(
    terminal: PayoutTerminal = Depends(get_payout_terminal),
) -> PayoutTerminal:
    from app.common.enums.merchants import TerminalStatus

    # Only ENABLED terminals move real money (TEST = no real financial ops per the
    # enum); creation is blocked for every other status.
    if terminal.status != TerminalStatus.ENABLED:
        raise ForbiddenException(
            f"Payout terminal is not active (status: {terminal.status.value})"
        )
    return terminal
