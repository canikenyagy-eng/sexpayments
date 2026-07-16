"""Phone normalisation for SBP / SIM requisites → one canonical +7XXXXXXXXXX."""
import pytest

from app.common.enums.payments import PaymentMethod
from app.common.phone import is_phone_method, normalize_phone


@pytest.mark.parametrize("raw", [
    "89991231212",
    "+7 (999) 123-12-12",
    "7 999 123 12 12",
    "9991231212",          # 10 digits, no country code
    "+79991231212",        # already canonical
    "  8-999-123-12-12  ",
    "tel:+7-999-123-1212",  # stray letters/symbols stripped
])
def test_normalize_phone_ru_variants(raw):
    assert normalize_phone(raw) == "+79991231212"


@pytest.mark.parametrize("raw", ["", None, "abc", "12345", "123456789012345"])
def test_normalize_phone_leaves_unrecognisable_untouched(raw):
    # Best-effort: never reject or lose data — anything that isn't a RU phone
    # comes back exactly as it went in.
    assert normalize_phone(raw) == raw


def test_is_phone_method():
    assert is_phone_method(PaymentMethod.SBP)
    assert is_phone_method(PaymentMethod.SIM)
    assert is_phone_method("sbp")
    assert is_phone_method("SIM")
    assert not is_phone_method(PaymentMethod.CARD)
    assert not is_phone_method("card")
