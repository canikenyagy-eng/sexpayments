import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from app.modules.payments.models import PaymentOption
from app.modules.payments.schemas import (
    MerchantPaymentOptionResponse,
    PaymentOptionResponse,
)
from app.modules.payments.service import PaymentOptionService
from app.common.enums.finances import Currency


@pytest.fixture
def mock_session():
    return AsyncMock()


@pytest.fixture
def mock_repository():
    return AsyncMock()


@pytest.mark.asyncio
async def test_get_active_options(mock_session, mock_repository):
    # Arrange
    service = PaymentOptionService(mock_session)
    service.repository = mock_repository
    
    mock_option_1 = PaymentOption(
        id=1,
        code="sber",
        name="Sberbank",
        logo_url="http://example.com/sber.png",
        supported_methods=["card", "sbp"],
        currency=Currency.RUB,
        is_active=True
    )
    mock_option_2 = PaymentOption(
        id=2,
        code="tinkoff",
        name="Tinkoff",
        logo_url="http://example.com/tinkoff.png",
        supported_methods=["card"],
        currency=Currency.RUB,
        is_active=True
    )
    
    mock_repository.get_active_options.return_value = [mock_option_1, mock_option_2]
    
    # Act
    result = await service.get_active_options()
    
    # Assert
    assert len(result) == 2
    assert result[0].name == "Sberbank"
    assert result[1].name == "Tinkoff"
    mock_repository.get_active_options.assert_called_once()


def test_merchant_payment_option_schema_hides_logo_url():
    """logo_url is an internal asset path: the merchant-facing schema must not
    expose it, while the trader/admin schema still does (role restriction)."""
    assert "logo_url" not in MerchantPaymentOptionResponse.model_fields
    assert "logo_url" in PaymentOptionResponse.model_fields

    option = PaymentOption(
        id=5,
        code="sber",
        name="Сбербанк",
        logo_url="/banks/sber.svg",
        supported_methods=["card"],
        currency=Currency.RUB,
        is_active=True,
    )
    dumped = MerchantPaymentOptionResponse.model_validate(option).model_dump()
    assert "logo_url" not in dumped
    # the rest of the contract stays intact
    assert dumped["id"] == 5
    assert dumped["code"] == "sber"
    assert dumped["supported_methods"] == ["card"]
    assert dumped["is_active"] is True
