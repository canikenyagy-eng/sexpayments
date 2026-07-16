import asyncio
import logging

from aiogram import F, Router
from aiogram.types import Message

from bot.keyboards.inline import BTN_BALANCE
from bot.services.api_client import (
    ApiClientError,
    BotLimits,
    MerchantBotApiClient,
    TerminalLimits,
)

router = Router()
logger = logging.getLogger(__name__)


_PAYMENT_METHOD_LABELS: dict[str, str] = {
    "sbp": "СБП",
    "card": "Карта",
    "sim": "SIM",
    "bank_transfer": "Банк. перевод",
    "crypto": "Crypto",
}


def _fmt(amount: float) -> str:
    return f"{amount:,.2f}".replace(",", " ")


def _fmt_int_money(amount: float) -> str:
    """Render a fiat amount as integer with thousand-separators (no kopeks)."""
    return f"{int(amount):,}".replace(",", " ")


def _method_label(code: str) -> str:
    return _PAYMENT_METHOD_LABELS.get(code, code.upper())


def _format_limits_section(limits: BotLimits) -> list[str]:
    """Render the «Лимиты» block — per-terminal, methods grouped inside.

    Shows ONE «available right now» number per method (already collapses
    daily + monthly caps), plus the per-order amount range and free slot
    count. Empty terminal (no eligible requisites) is surfaced so the
    merchant understands why nothing can be created.
    """
    if not limits.terminals:
        return []

    lines: list[str] = ["", "📊 <b>Лимиты по терминалам</b>"]
    for term in limits.terminals:
        title = term.name or f"Терминал #{term.id}"
        lines.append(f"<b>🏦 {title}</b>")
        if not term.methods:
            lines.append("  <i>нет доступных реквизитов</i>")
            continue
        for m in term.methods:
            label = _method_label(m.payment_method)
            slots = (
                "∞" if m.concurrent_slots is None else str(m.concurrent_slots)
            )
            lines.append(
                f"  ‣ <b>{label}</b>: доступно сейчас "
                f"{_fmt_int_money(m.available)} {m.currency}"
            )
            # max_amount = "the largest single order that will pass NOW".
            # When all requisites are saturated max can drop below min (or
            # to 0). Surface this explicitly instead of printing "100–0".
            if m.max_amount <= 0 or m.max_amount < m.min_amount:
                lines.append(
                    f"    лимит исчерпан, "
                    f"слотов: {slots} ({m.requisites_count} акт. рекв.)"
                )
            else:
                lines.append(
                    f"    одна сделка {_fmt_int_money(m.min_amount)}–"
                    f"{_fmt_int_money(m.max_amount)} {m.currency} (макс. сейчас), "
                    f"слотов: {slots} ({m.requisites_count} акт. рекв.)"
                )
    return lines


@router.message(F.text == BTN_BALANCE)
async def show_balance(message: Message, api_client: MerchantBotApiClient) -> None:
    tg_user_id = message.from_user.id if message.from_user else 0

    # Fetch balances and limits in parallel — limits failure must not block
    # the balance view, so we wrap with return_exceptions=True.
    try:
        balances_res, limits_res = await asyncio.gather(
            api_client.get_balances(tg_user_id),
            api_client.get_limits(tg_user_id),
            return_exceptions=True,
        )
    except Exception as exc:  # defensive: gather itself shouldn't raise here
        await message.answer(f"Не удалось получить баланс.\n\n{exc}")
        return

    if isinstance(balances_res, ApiClientError):
        await message.answer(f"Не удалось получить баланс.\n\n{balances_res}")
        return
    if isinstance(balances_res, BaseException):
        # Unexpected error — still surface to the user.
        await message.answer(f"Не удалось получить баланс.\n\n{balances_res}")
        return

    balances = balances_res

    cur = balances.currency

    lines: list[str] = []
    lines.append("💰 <b>Общий баланс</b>")
    lines.append(f"Доступно: <b>{_fmt(balances.total_work)} {cur}</b>")
    if balances.total_escrow > 0:
        lines.append(f"В резерве: {_fmt(balances.total_escrow)} {cur}")

    if balances.owner_work > 0 or balances.owner_escrow > 0:
        lines.append("")
        lines.append("🏷 <b>На счёте мерчанта</b>")
        lines.append(f"Доступно: {_fmt(balances.owner_work)} {cur}")
        if balances.owner_escrow > 0:
            lines.append(f"В резерве: {_fmt(balances.owner_escrow)} {cur}")

    if balances.terminals:
        lines.append("")
        lines.append("🏦 <b>По терминалам</b>")
        for t in balances.terminals:
            title = t.name or f"Терминал #{t.id}"
            extra = (
                f" (резерв {_fmt(t.escrow)} {t.currency})"
                if t.escrow > 0
                else ""
            )
            lines.append(f"• {title}: {_fmt(t.work)} {t.currency}{extra}")
    else:
        lines.append("")
        lines.append("У вас нет доступных терминалов.")

    # Append the limits block. If it failed, log + show a one-liner so the
    # user knows the data is missing rather than silently absent.
    if isinstance(limits_res, ApiClientError):
        logger.warning("get_limits failed for tg_user %s: %s", tg_user_id, limits_res)
        lines.append("")
        lines.append("📊 <i>Лимиты сейчас недоступны.</i>")
    elif isinstance(limits_res, BaseException):
        logger.exception("Unexpected error fetching limits", exc_info=limits_res)
        lines.append("")
        lines.append("📊 <i>Лимиты сейчас недоступны.</i>")
    else:
        lines.extend(_format_limits_section(limits_res))

    await message.answer("\n".join(lines))
