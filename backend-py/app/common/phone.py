"""Phone-number normalisation for phone-based requisites (SBP / SIM).

Traders enter phone numbers in many shapes (``8 999 …``, ``+7 (999) …``, a bare
10 digits, …); we store and display ONE canonical Russian form,
``+7XXXXXXXXXX``, regardless of how it was typed. Card requisites (``card``) keep
their raw ``account_number`` untouched.
"""
import re

from app.common.enums.payments import PaymentMethod

_PHONE_METHOD_VALUES = frozenset({PaymentMethod.SBP.value, PaymentMethod.SIM.value})


def is_phone_method(method) -> bool:
    """True when a requisite's ``account_number`` is a phone number (SBP / SIM)
    rather than a card number. Accepts the ``PaymentMethod`` enum or its value."""
    value = getattr(method, "value", method)
    return str(value).lower() in _PHONE_METHOD_VALUES


def normalize_phone(raw: str) -> str:
    """Canonicalise a Russian phone to ``+7XXXXXXXXXX`` regardless of how it was
    typed (spaces / dashes / parens / leading ``8``, ``7`` or ``+7`` / a bare
    10 digits). If the input isn't a recognisable RU phone it's returned
    unchanged — best-effort, so nothing is ever rejected or lost."""
    if not raw:
        return raw
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits[0] in ("7", "8"):
        digits = "7" + digits[1:]
    elif len(digits) == 10:
        digits = "7" + digits
    else:
        return raw
    return "+" + digits
