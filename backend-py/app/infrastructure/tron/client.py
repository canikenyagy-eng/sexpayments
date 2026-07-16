"""Minimal TronGrid client for verifying TRC20 (USDT) deposits by tx hash.

Used by the admin "top-up by hash" flow: given a transaction hash we fetch the
transaction receipt from TronGrid and extract its TRC20 ``Transfer`` events
(token contract, to-address, amount). Addresses are returned in base58
(``T...``) form so the caller can compare/display them.

No third-party TRON SDK — the only two things we need (a couple of read-only
HTTP calls and base58check address encoding) are implemented inline to keep the
dependency surface small.
"""
import hashlib
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import List, Optional

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# keccak256("Transfer(address,address,uint256)") — the TRC20/ERC20 Transfer topic.
_TRANSFER_TOPIC = "ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"

# USDT-TRC20 has 6 decimals.
USDT_DECIMALS = 6


class TronGridError(Exception):
    """TronGrid was unreachable or returned an unusable response."""


def _b58check_encode(payload: bytes) -> str:
    """base58check-encode a raw address payload (0x41 + 20 bytes)."""
    checksum = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    data = payload + checksum
    n = int.from_bytes(data, "big")
    out = ""
    while n > 0:
        n, rem = divmod(n, 58)
        out = _B58_ALPHABET[rem] + out
    # Preserve leading zero bytes as '1'.
    for b in data:
        if b == 0:
            out = "1" + out
        else:
            break
    return out


def hex_to_tron_address(hex_addr: str) -> str:
    """Convert a TRON address in hex form to its base58 ``T...`` representation.

    Accepts the shapes TronGrid emits: a 21-byte hex with the ``41`` prefix, a
    bare 20-byte hex (log ``address``), or a 32-byte left-padded topic word.
    """
    h = (hex_addr or "").lower().removeprefix("0x")
    if len(h) == 64:            # 32-byte topic word → last 20 bytes
        h = "41" + h[24:]
    elif len(h) == 40:          # bare 20-byte address
        h = "41" + h
    elif len(h) == 42 and h.startswith("41"):
        pass                    # already 21 bytes with prefix
    elif len(h) > 40:           # defensive: take the trailing 20 bytes
        h = "41" + h[-40:]
    else:
        raise TronGridError(f"Unrecognised TRON address hex: {hex_addr!r}")
    return _b58check_encode(bytes.fromhex(h))


@dataclass
class Trc20Transfer:
    contract_address: str  # base58 (T...)
    from_address: str      # base58
    to_address: str        # base58
    amount: Decimal        # token units (decimals already applied)


@dataclass
class TxInfo:
    found: bool
    confirmed: bool                       # included in a block
    success: bool                         # receipt result == SUCCESS
    transfers: List[Trc20Transfer] = field(default_factory=list)


class TronGridClient:
    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: float = 15.0,
    ):
        settings = get_settings()
        self._base = (base_url or settings.TRONGRID_BASE_URL or "https://api.trongrid.io").rstrip("/")
        self._api_key = api_key if api_key is not None else settings.TRONGRID_API_KEY
        self._timeout = timeout

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["TRON-PRO-API-KEY"] = self._api_key
        return headers

    async def get_transaction_info(self, tx_hash: str) -> TxInfo:
        """Fetch a transaction's receipt and decode every TRC20 ``Transfer`` event.

        Raises ``TronGridError`` on transport / decode failure. A syntactically
        valid but non-existent hash comes back as ``TxInfo(found=False)``.
        """
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    f"{self._base}/wallet/gettransactioninfobyid",
                    json={"value": tx_hash},
                    headers=self._headers(),
                )
                resp.raise_for_status()
                info = resp.json() if resp.content else {}
        except httpx.HTTPError as exc:
            logger.warning("TronGrid gettransactioninfobyid failed: %s", exc)
            raise TronGridError(str(exc)) from exc

        if not isinstance(info, dict) or not info.get("id"):
            return TxInfo(found=False, confirmed=False, success=False)

        confirmed = info.get("blockNumber") is not None
        receipt = info.get("receipt") or {}
        # A TRC20 transfer is a contract call: SUCCESS means it didn't revert.
        # Some nodes omit ``result`` on plain success — treat "present Transfer
        # log" as the real proof and only reject an explicit non-SUCCESS result.
        result = receipt.get("result")
        success = result in (None, "SUCCESS")

        transfers: List[Trc20Transfer] = []
        for entry in info.get("log") or []:
            topics = entry.get("topics") or []
            if len(topics) < 3 or (topics[0] or "").lower() != _TRANSFER_TOPIC:
                continue
            try:
                contract = hex_to_tron_address(entry.get("address", ""))
                from_addr = hex_to_tron_address(topics[1])
                to_addr = hex_to_tron_address(topics[2])
                raw = int(entry.get("data") or "0", 16)
            except (TronGridError, ValueError) as exc:
                logger.warning("Skipping unparsable transfer log in %s: %s", tx_hash, exc)
                continue
            amount = Decimal(raw) / (Decimal(10) ** USDT_DECIMALS)
            transfers.append(
                Trc20Transfer(
                    contract_address=contract,
                    from_address=from_addr,
                    to_address=to_addr,
                    amount=amount,
                )
            )

        return TxInfo(found=True, confirmed=confirmed, success=success, transfers=transfers)
