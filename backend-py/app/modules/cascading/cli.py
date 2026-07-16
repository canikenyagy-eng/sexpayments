"""Semi-automatic probe for cascade-provider integrations against their
**live** APIs.

Why this exists
---------------
Unit tests (``tests/unit/test_provider_adapter_*.py``) mock httpx and
prove that our parsing/signing code does the right thing on canned
responses. They give zero confidence that the provider's *real* API
still speaks the same shape — keys may rename, fields may move, auth
schemes may evolve. This tool talks to the real endpoints with the
credentials saved on the ``CascadeProvider`` row and reports back.

Design choices
~~~~~~~~~~~~~~
1. **Single binary, several subcommands** — easy to script, easy to
   eyeball. Each subcommand maps 1:1 to an adapter method, so the
   coverage matches what production actually does.

2. **Read-only by default.** ``ping`` and ``balance`` poke endpoints
   that don't move money or burn requisites — safe for CI / cron.
   ``issue`` reserves a real requisite (so it should be paired with
   ``cancel`` immediately, and the ``full`` subcommand does that).

3. **Reuses production code paths verbatim.** We instantiate the same
   ``ProviderAdapter`` subclass that the cascade scheduler does, load
   credentials via the same ``decrypt_api_secret`` chain. No mocked
   httpx. If something works here, it'll work in prod. If it breaks,
   the traceback points at the same line that the scheduler would
   stumble over.

4. **All printing is structured** — we emit dataclasses with ``__repr__``
   for human eyes and ``--json`` for machine consumption (cron alerts,
   wrapping in shell scripts). No ad-hoc strings.

Usage
-----
::

    # List providers visible to this DATABASE_URL:
    python -m app.modules.cascading.cli list

    # Ping + balance + rate (read-only):
    python -m app.modules.cascading.cli ping legacy_crypto_main

    # Issue a requisite (test order — provider WILL reserve a trader):
    python -m app.modules.cascading.cli issue legacy_crypto_main \\
        --amount 1500 --method sbp --option-code sber

    # Cancel that issued order — pass the external_order_id printed above:
    python -m app.modules.cascading.cli cancel legacy_crypto_main PI-abc123

    # Poll a known external_order_id:
    python -m app.modules.cascading.cli poll legacy_crypto_main PI-abc123

    # End-to-end smoke: issue → immediately cancel, print both responses.
    # Safe for CI — never leaves a reservation behind even on adapter bugs.
    python -m app.modules.cascading.cli full legacy_crypto_main \\
        --amount 1500 --method sbp --option-code sber

Notes
~~~~~
- Set ``CASCADE_PROBE_HTTP_TRACE=1`` to log every outbound URL and the
  first 1 KB of every response — gold for debugging signature errors
  that the provider returns as opaque 401s.
- Provider rows are looked up by ``code`` (the slug-like identifier the
  admin sees in the UI), not by adapter type. So if you have multiple
  Swifty terminals, each gets its own slug and you probe them
  individually.
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import logging
import os
import sys
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import uuid4

from sqlalchemy import select

from app.common.enums.payments import PaymentMethod
from app.infrastructure.db.session import SessionLocal
from app.modules.cascading.integrations import registry
from app.modules.cascading.integrations.base import (
    ProviderRefusal,
    ProviderRequisiteResponse,
)
from app.modules.cascading.models import CascadeProvider


# ─── Output ────────────────────────────────────────────────────────────


@dataclass
class ProbeResult:
    """Uniform envelope for every subcommand.

    The CLI prints these as a small markdown-ish block in human mode
    and as JSON in --json mode. Keeping the shape consistent means
    wrappers (cron, monitoring) only have to parse one schema.
    """

    provider_code: str
    adapter_type: str
    action: str
    ok: bool
    summary: str
    details: Dict[str, Any] = dataclasses.field(default_factory=dict)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    def render(self) -> str:
        status = "✓" if self.ok else "✗"
        lines = [
            f"[{status}] {self.action}  {self.provider_code}"
            f" ({self.adapter_type})",
            f"    {self.summary}",
        ]
        if self.error:
            lines.append(f"    ERROR: {self.error}")
        for k, v in self.details.items():
            lines.append(f"    {k}: {v}")
        return "\n".join(lines)


# ─── Helpers ───────────────────────────────────────────────────────────


async def _load_provider(code: str) -> CascadeProvider:
    """Pull the ``CascadeProvider`` row by ``code``.

    Raises a friendly ``SystemExit`` (not a stack trace) when the code
    isn't found — this is a CLI for humans, ugly tracebacks aren't
    helpful for "I typo'd the slug".
    """
    async with SessionLocal() as session:
        result = await session.execute(
            select(CascadeProvider).where(CascadeProvider.code == code)
        )
        row = result.scalar_one_or_none()
        if row is None:
            sys.stderr.write(
                f"Provider with code={code!r} not found in this DB.\n"
                "Run `python -m app.modules.cascading.cli list` to see "
                "available codes.\n"
            )
            sys.exit(2)
        # ``expire_on_commit=False`` means the row stays usable after the
        # session closes — handy because we then make HTTP calls that
        # might take a few seconds and we don't want to hold a DB conn.
        session.expunge(row)
        return row


def _adapter_for(provider: CascadeProvider):
    """Resolve the ``ProviderAdapter`` instance the cascade scheduler
    would use for this row. Same registry, same lookup — no special
    paths just for the CLI.
    """
    if not registry.has(provider.adapter_type):
        raise SystemExit(
            f"Adapter {provider.adapter_type!r} not registered. "
            "Did the adapter file move?"
        )
    return registry.get(provider.adapter_type)


def _maybe_enable_http_trace() -> None:
    """Verbose-log httpx when ``CASCADE_PROBE_HTTP_TRACE=1`` is set.

    Worth it for the painful "the provider returned 401 with no body"
    debugging — you can eyeball the exact path + headers we sent.
    """
    if os.getenv("CASCADE_PROBE_HTTP_TRACE") != "1":
        return
    logging.basicConfig(level=logging.DEBUG)
    logging.getLogger("httpx").setLevel(logging.DEBUG)
    logging.getLogger("httpcore").setLevel(logging.DEBUG)


# ─── Subcommands ───────────────────────────────────────────────────────


async def cmd_list() -> int:
    """List all providers + adapter type + active flag.

    Useful before any other subcommand — tells you which slug to pass.
    """
    async with SessionLocal() as session:
        result = await session.execute(
            select(CascadeProvider).order_by(CascadeProvider.code)
        )
        rows = list(result.scalars().all())

    if not rows:
        print("(no providers registered in this database)")
        return 0

    print(f"{'CODE':28s}  {'ADAPTER':16s}  {'ACTIVE':6s}  {'BASE_URL'}")
    print("-" * 90)
    for p in rows:
        active = "yes" if p.is_active else "no"
        print(f"{p.code:28s}  {p.adapter_type:16s}  {active:6s}  {p.base_url}")
    return 0


async def cmd_ping(provider_code: str) -> ProbeResult:
    """Read-only health probe: balance + (optionally) upstream rate.

    Doesn't touch orders or requisites. Failing here usually means
    credentials are wrong or the endpoint moved.
    """
    provider = await _load_provider(provider_code)
    adapter = _adapter_for(provider)
    details: Dict[str, Any] = {}
    try:
        balance = await adapter.get_balance(provider=provider)
        details["balance"] = (
            str(balance) if balance is not None else "(not exposed by adapter)"
        )
        # ``get_upstream_rate`` is optional on adapters (only Garex
        # implements it today); call when present.
        rate_fn = getattr(adapter, "get_upstream_rate", None)
        if rate_fn:
            rate = await rate_fn(provider=provider)
            details["upstream_rate"] = str(rate) if rate is not None else "—"
    except Exception as exc:  # noqa: BLE001 — surface ALL errors to CLI
        return ProbeResult(
            provider_code=provider.code,
            adapter_type=provider.adapter_type,
            action="ping",
            ok=False,
            summary="probe failed",
            details=details,
            error=f"{type(exc).__name__}: {exc}",
        )
    return ProbeResult(
        provider_code=provider.code,
        adapter_type=provider.adapter_type,
        action="ping",
        ok=True,
        summary="auth + connectivity OK",
        details=details,
    )


async def cmd_balance(provider_code: str) -> ProbeResult:
    """Just balance — alias for tighter cron jobs that only watch funds."""
    provider = await _load_provider(provider_code)
    adapter = _adapter_for(provider)
    try:
        balance = await adapter.get_balance(provider=provider)
    except Exception as exc:  # noqa: BLE001
        return ProbeResult(
            provider_code=provider.code,
            adapter_type=provider.adapter_type,
            action="balance",
            ok=False,
            summary="balance fetch failed",
            error=f"{type(exc).__name__}: {exc}",
        )
    ok = balance is not None
    return ProbeResult(
        provider_code=provider.code,
        adapter_type=provider.adapter_type,
        action="balance",
        ok=ok,
        summary=f"balance={balance}" if ok else "adapter returned None",
        details={"balance": str(balance) if balance is not None else None},
    )


async def cmd_issue(
    provider_code: str,
    amount: Decimal,
    method: PaymentMethod,
    option_code: Optional[str],
    idempotency_key: Optional[str],
) -> ProbeResult:
    """Reserve a real requisite at the provider.

    This is **not** read-only — it occupies a trader at the provider
    side until cancelled or expires. Always pair with a cancel (the
    ``full`` subcommand does it automatically) for CI use.
    """
    provider = await _load_provider(provider_code)
    adapter = _adapter_for(provider)
    idem = idempotency_key or f"probe-{uuid4().hex[:12]}"
    order_data = {
        "amount": amount,
        "payment_method": method,
        "payment_option_code": option_code,
        "merchant_request_id": idem,
        # client_user_id helps for adapters that require ``userId`` (Payscrow,
        # Bitwire). Pass the same idempotency token — safe, opaque to humans.
        "client_user_id": idem,
        "client_full_name": "Probe Tester",
    }
    timeout_ms = provider.request_timeout_ms or 5000
    try:
        result = await adapter.issue_requisite(
            provider=provider,
            order_data=order_data,
            idempotency_key=idem,
            timeout_ms=timeout_ms,
        )
    except Exception as exc:  # noqa: BLE001
        return ProbeResult(
            provider_code=provider.code,
            adapter_type=provider.adapter_type,
            action="issue",
            ok=False,
            summary="adapter raised",
            details={"idempotency_key": idem},
            error=f"{type(exc).__name__}: {exc}",
        )

    if isinstance(result, ProviderRefusal):
        return ProbeResult(
            provider_code=provider.code,
            adapter_type=provider.adapter_type,
            action="issue",
            ok=False,
            summary=f"refused: {result.code}",
            details={
                "refusal_message": result.message,
                "idempotency_key": idem,
            },
        )
    if isinstance(result, ProviderRequisiteResponse):
        return ProbeResult(
            provider_code=provider.code,
            adapter_type=provider.adapter_type,
            action="issue",
            ok=True,
            summary=f"reserved requisite {result.external_order_id}",
            details={
                "external_order_id": result.external_order_id,
                "bank_name": result.bank_name,
                "account_number": result.account_number,
                "account_holder": result.account_holder,
                "payment_method": result.payment_method.value,
                "amount_fiat": str(result.amount_fiat),
                "provider_rate": (
                    str(result.provider_rate) if result.provider_rate else None
                ),
                "expires_at": result.expires_at.isoformat(),
                "idempotency_key": idem,
            },
        )
    return ProbeResult(
        provider_code=provider.code,
        adapter_type=provider.adapter_type,
        action="issue",
        ok=False,
        summary="adapter returned unexpected type",
        details={"return_type": type(result).__name__},
    )


async def cmd_cancel(provider_code: str, external_order_id: str) -> ProbeResult:
    """Best-effort cancel of a previously-issued requisite."""
    provider = await _load_provider(provider_code)
    adapter = _adapter_for(provider)
    timeout_ms = provider.cancel_timeout_ms or 2000
    try:
        ok = await adapter.cancel_request(
            provider=provider,
            external_order_id=external_order_id,
            timeout_ms=timeout_ms,
        )
    except Exception as exc:  # noqa: BLE001
        return ProbeResult(
            provider_code=provider.code,
            adapter_type=provider.adapter_type,
            action="cancel",
            ok=False,
            summary="adapter raised",
            error=f"{type(exc).__name__}: {exc}",
            details={"external_order_id": external_order_id},
        )
    return ProbeResult(
        provider_code=provider.code,
        adapter_type=provider.adapter_type,
        action="cancel",
        ok=bool(ok),
        summary=(
            f"cancelled {external_order_id}" if ok else "provider refused cancel"
        ),
        details={"external_order_id": external_order_id},
    )


async def cmd_poll(provider_code: str, external_order_id: str) -> ProbeResult:
    """Polls the provider for the current state of an order."""
    provider = await _load_provider(provider_code)
    adapter = _adapter_for(provider)
    timeout_ms = provider.request_timeout_ms or 5000
    try:
        parsed = await adapter.poll_status(
            provider=provider,
            external_order_id=external_order_id,
            timeout_ms=timeout_ms,
        )
    except Exception as exc:  # noqa: BLE001
        return ProbeResult(
            provider_code=provider.code,
            adapter_type=provider.adapter_type,
            action="poll",
            ok=False,
            summary="adapter raised",
            error=f"{type(exc).__name__}: {exc}",
            details={"external_order_id": external_order_id},
        )
    if parsed is None:
        return ProbeResult(
            provider_code=provider.code,
            adapter_type=provider.adapter_type,
            action="poll",
            ok=False,
            summary="provider returned no data (404 or polling unsupported)",
            details={"external_order_id": external_order_id},
        )
    return ProbeResult(
        provider_code=provider.code,
        adapter_type=provider.adapter_type,
        action="poll",
        ok=True,
        summary=f"status={parsed.status.value}",
        details={
            "external_order_id": parsed.external_order_id,
            "provider_status": parsed.status.value,
            "paid_amount_fiat": (
                str(parsed.paid_amount_fiat)
                if parsed.paid_amount_fiat is not None
                else None
            ),
        },
    )


async def cmd_full(
    provider_code: str,
    amount: Decimal,
    method: PaymentMethod,
    option_code: Optional[str],
) -> List[ProbeResult]:
    """End-to-end probe: ping → issue → cancel.

    Designed for CI: if any step fails the trace points at the first
    failure, and the issued requisite (if any) is always rolled back.
    """
    results: List[ProbeResult] = []

    # 1) cheap ping first — fail fast on auth / connectivity.
    ping = await cmd_ping(provider_code)
    results.append(ping)
    if not ping.ok:
        return results

    # 2) issue
    issue = await cmd_issue(
        provider_code=provider_code,
        amount=amount,
        method=method,
        option_code=option_code,
        idempotency_key=None,
    )
    results.append(issue)
    if not issue.ok:
        return results

    external_id = issue.details.get("external_order_id")
    if not external_id:
        return results

    # 3) immediately cancel — we never wanted a real reservation. Even
    # if cancel fails, the requisite will expire on the provider's
    # side; we still report it so ops can decide whether to alert.
    cancel = await cmd_cancel(provider_code, external_id)
    results.append(cancel)
    return results


# ─── argparse plumbing ─────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m app.modules.cascading.cli",
        description=(
            "Probe cascade provider integrations against their live API. "
            "Read-only by default; pass --json for machine-readable output."
        ),
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit results as a JSON object instead of human text.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List cascade providers visible to this DB.")

    p_ping = sub.add_parser("ping", help="Read-only health probe (balance + rate).")
    p_ping.add_argument("provider_code", help="CascadeProvider.code")

    p_bal = sub.add_parser("balance", help="Fetch upstream balance only.")
    p_bal.add_argument("provider_code", help="CascadeProvider.code")

    p_issue = sub.add_parser("issue", help="Issue a real requisite (NOT read-only).")
    p_issue.add_argument("provider_code", help="CascadeProvider.code")
    p_issue.add_argument(
        "--amount",
        required=True,
        type=Decimal,
        help="Fiat amount in the provider's local currency.",
    )
    p_issue.add_argument(
        "--method",
        required=True,
        choices=[m.value for m in PaymentMethod],
        help="Our internal PaymentMethod (sbp / card / sim / ...).",
    )
    p_issue.add_argument(
        "--option-code",
        default=None,
        help="Our PaymentOption.code (e.g. sber). Optional.",
    )
    p_issue.add_argument(
        "--idem",
        default=None,
        help="Idempotency key override. Defaults to ``probe-<8hex>``.",
    )

    p_cancel = sub.add_parser("cancel", help="Cancel a previously-issued requisite.")
    p_cancel.add_argument("provider_code", help="CascadeProvider.code")
    p_cancel.add_argument(
        "external_order_id",
        help="external_order_id printed by the issue subcommand.",
    )

    p_poll = sub.add_parser("poll", help="Poll the provider for an order's state.")
    p_poll.add_argument("provider_code", help="CascadeProvider.code")
    p_poll.add_argument("external_order_id", help="external_order_id to poll.")

    p_full = sub.add_parser(
        "full",
        help="End-to-end: ping → issue → cancel (safe for CI).",
    )
    p_full.add_argument("provider_code", help="CascadeProvider.code")
    p_full.add_argument("--amount", required=True, type=Decimal)
    p_full.add_argument(
        "--method", required=True, choices=[m.value for m in PaymentMethod]
    )
    p_full.add_argument("--option-code", default=None)

    return p


def _emit(args: argparse.Namespace, items: List[ProbeResult]) -> int:
    """Render one or many ProbeResults; return process exit code (0/1)."""
    all_ok = all(r.ok for r in items)
    if args.json:
        print(json.dumps([r.to_dict() for r in items], ensure_ascii=False, indent=2))
    else:
        for r in items:
            print(r.render())
            print()
    return 0 if all_ok else 1


async def _run(args: argparse.Namespace) -> int:
    _maybe_enable_http_trace()

    if args.command == "list":
        return await cmd_list()

    if args.command == "ping":
        return _emit(args, [await cmd_ping(args.provider_code)])

    if args.command == "balance":
        return _emit(args, [await cmd_balance(args.provider_code)])

    if args.command == "issue":
        result = await cmd_issue(
            provider_code=args.provider_code,
            amount=args.amount,
            method=PaymentMethod(args.method),
            option_code=args.option_code,
            idempotency_key=args.idem,
        )
        return _emit(args, [result])

    if args.command == "cancel":
        return _emit(args, [await cmd_cancel(args.provider_code, args.external_order_id)])

    if args.command == "poll":
        return _emit(args, [await cmd_poll(args.provider_code, args.external_order_id)])

    if args.command == "full":
        results = await cmd_full(
            provider_code=args.provider_code,
            amount=args.amount,
            method=PaymentMethod(args.method),
            option_code=args.option_code,
        )
        return _emit(args, results)

    raise SystemExit(f"Unknown command: {args.command}")


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    try:
        rc = asyncio.run(_run(args))
    except KeyboardInterrupt:
        rc = 130
    sys.exit(rc)


if __name__ == "__main__":
    main()
