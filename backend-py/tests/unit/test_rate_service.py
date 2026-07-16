import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from app.modules.rates.models import RateConfig
from app.modules.rates.service import RateService
from app.modules.rates.schemas import RateConfigCreate, RateConfigUpdate
from app.common.enums.rates import RateSource, OrderBookSide
from app.common.enums.finances import Currency


class AsyncContextManagerMock:
    async def __aenter__(self):
        return self
    async def __aexit__(self, exc_type, exc, tb):
        pass

@pytest.fixture
def mock_session():
    session = MagicMock()
    session.begin.return_value = AsyncContextManagerMock()
    return session


@pytest.fixture
def mock_repository():
    return AsyncMock()


@pytest.fixture
def service(mock_session, mock_repository):
    svc = RateService(mock_session)
    svc.repository = mock_repository
    svc.audit_log = AsyncMock()
    return svc


@pytest.fixture
def sample_rate_config():
    return RateConfig(
        id=1,
        name="Test Bybit Rate",
        source=RateSource.BYBIT,
        side=OrderBookSide.BUY,
        position=2,
        payment_methods=["75"],
        fiat_currency=Currency.RUB,
        crypto_currency="USDT",
        update_interval_seconds=60,
        is_active=True,
        current_rate=100.5,
        last_updated_at=datetime.utcnow()
    )


@pytest.mark.asyncio
async def test_create_config(service, mock_repository, sample_rate_config):
    # Arrange
    create_schema = RateConfigCreate(
        name="Test Bybit Rate",
        source=RateSource.BYBIT,
        side=OrderBookSide.BUY,
        position=2,
        payment_methods=["75"],
        fiat_currency=Currency.RUB,
        crypto_currency="USDT",
        update_interval_seconds=60,
        is_active=True
    )
    
    mock_repository.create.return_value = sample_rate_config
    
    # Act
    result = await service.create_config(create_schema, admin_user_id=42)
    
    # Assert
    assert result.id == 1
    assert result.name == "Test Bybit Rate"
    mock_repository.create.assert_called_once_with(create_schema.model_dump())
    service.audit_log.assert_called_once_with(
        action="create_rate_config",
        entity_type="rate_config",
        entity_id=1,
        user_id=42,
        new_values=create_schema.model_dump(),
    )


@pytest.mark.asyncio
async def test_update_config(service, mock_repository, sample_rate_config):
    # Arrange
    mock_repository.get.return_value = sample_rate_config
    mock_repository.update.return_value = sample_rate_config
    
    update_schema = RateConfigUpdate(is_active=False, position=3)
    
    # Act
    result = await service.update_config(1, update_schema, admin_user_id=42)
    
    # Assert
    assert result == sample_rate_config
    mock_repository.update.assert_called_once_with(1, update_schema.model_dump(exclude_unset=True))
    service.audit_log.assert_called_once()
    
    call_args = service.audit_log.call_args[1]
    assert call_args["action"] == "update_rate_config"
    assert call_args["entity_id"] == 1
    assert call_args["user_id"] == 42
    assert call_args["old_values"]["is_active"] is True
    assert call_args["old_values"]["position"] == 2
    assert call_args["new_values"]["is_active"] is False
    assert call_args["new_values"]["position"] == 3


@pytest.mark.asyncio
async def test_delete_config(service, mock_repository, sample_rate_config):
    # Arrange
    mock_repository.get.return_value = sample_rate_config
    
    # Act
    await service.delete_config(1, admin_user_id=42)
    
    # Assert
    mock_repository.delete.assert_called_once_with(1)
    service.audit_log.assert_called_once_with(
        action="delete_rate_config",
        entity_type="rate_config",
        entity_id=1,
        user_id=42,
        old_values={
            "crypto_currency": "USDT",
            "fiat_currency": Currency.RUB.value,
            "source": RateSource.BYBIT.value,
            "is_active": True,
        },
    )


@pytest.mark.asyncio
@patch("aiohttp.ClientSession.post")
async def test_fetch_bybit_rate_success(mock_post, service, mock_repository, sample_rate_config):
    # Arrange
    service = RateService(mock_session)
    service.repository = mock_repository
    
    # Mock Bybit API response
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.json.return_value = {
        "ret_code": 0,
        "result": {
            "items": [
                {"price": "100.1"}, # Position 1
                {"price": "100.5"}, # Position 2 (Our target)
                {"price": "100.9"}  # Position 3
            ]
        }
    }
    
    # Setup context manager for mock_post
    mock_post.return_value.__aenter__.return_value = mock_response
    
    # Act
    rate = await service.fetch_bybit_rate(sample_rate_config)
    
    # Assert
    assert rate == 100.5
    mock_post.assert_called_once()
    call_args = mock_post.call_args[1]["json"]
    assert call_args["side"] == "1"  # BUY
    assert call_args["currencyId"] == "RUB"
    assert call_args["payment"] == ["75"]


@pytest.mark.asyncio
@patch("aiohttp.ClientSession.post")
async def test_fetch_bybit_rate_not_enough_items(mock_post, service, mock_repository, sample_rate_config):
    # Arrange
    # Mock Bybit API response with only 1 item (we need position 2)
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.json.return_value = {
        "ret_code": 0,
        "result": {
            "items": [
                {"price": "100.1"}
            ]
        }
    }
    
    mock_post.return_value.__aenter__.return_value = mock_response
    
    # Act
    rate = await service.fetch_bybit_rate(sample_rate_config)
    
    # Assert
    assert rate is None


@pytest.mark.asyncio
@patch.object(RateService, "fetch_bybit_rate")
async def test_update_rate_success(mock_fetch, service, mock_repository, sample_rate_config):
    # Arrange
    mock_repository.get.return_value = sample_rate_config
    mock_fetch.return_value = 101.2

    # Act
    new_rate = await service.update_rate(1)

    # Assert
    assert new_rate == 101.2
    mock_repository.get.assert_called_once_with(1)
    mock_fetch.assert_called_once_with(sample_rate_config)
    mock_repository.update.assert_called_once()

    update_args = mock_repository.update.call_args[0]
    assert update_args[0] == 1
    assert update_args[1]["current_rate"] == 101.2
    assert "last_updated_at" in update_args[1]


# ────────────────────────────────────────────────────────────────
# get_config / list_configs / get_active_configs — tiny pass-throughs
# ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_config_success(service, mock_repository, sample_rate_config):
    mock_repository.get.return_value = sample_rate_config

    result = await service.get_config(1)

    assert result is sample_rate_config
    mock_repository.get.assert_called_once_with(1)


@pytest.mark.asyncio
async def test_get_config_not_found(service, mock_repository):
    from app.core.exceptions import NotFoundException
    mock_repository.get.return_value = None

    with pytest.raises(NotFoundException, match="Rate config 99 not found"):
        await service.get_config(99)


@pytest.mark.asyncio
async def test_list_configs_passthrough(service, mock_repository, sample_rate_config):
    mock_repository.get_all.return_value = [sample_rate_config]

    result = await service.list_configs()

    assert result == [sample_rate_config]
    mock_repository.get_all.assert_called_once()


@pytest.mark.asyncio
async def test_get_active_configs_passthrough(service, mock_repository, sample_rate_config):
    mock_repository.get_active_configs.return_value = [sample_rate_config]

    result = await service.get_active_configs()

    assert result == [sample_rate_config]
    mock_repository.get_active_configs.assert_called_once()


# ────────────────────────────────────────────────────────────────
# fetch_rapira_rate — second provider; mirrors fetch_bybit_rate.
# ────────────────────────────────────────────────────────────────


@pytest.fixture
def sample_rapira_config():
    return RateConfig(
        id=2,
        name="Test Rapira Rate",
        source=RateSource.RAPIRA,
        side=OrderBookSide.BUY,
        position=2,
        payment_methods=[],
        fiat_currency=Currency.RUB,
        crypto_currency="USDT",
        update_interval_seconds=60,
        is_active=True,
        current_rate=99.0,
        last_updated_at=datetime.utcnow(),
    )


@pytest.mark.asyncio
@patch("aiohttp.ClientSession.post")
async def test_fetch_rapira_rate_buy_picks_bid_at_position(mock_post, service, sample_rapira_config):
    """BUY side reads from `bid` array; position=2 is 1-indexed → second item."""
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.json.return_value = {
        "bid": {"items": [{"price": "98.0"}, {"price": "97.5"}, {"price": "97.0"}]},
        "ask": {"items": [{"price": "99.0"}]},
    }
    mock_post.return_value.__aenter__.return_value = mock_response

    rate = await service.fetch_rapira_rate(sample_rapira_config)

    assert rate == 97.5


@pytest.mark.asyncio
@patch("aiohttp.ClientSession.post")
async def test_fetch_rapira_rate_sell_picks_ask(mock_post, service, sample_rapira_config):
    sample_rapira_config.side = OrderBookSide.SELL
    sample_rapira_config.position = 1

    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.json.return_value = {
        "bid": {"items": [{"price": "98.0"}]},
        "ask": {"items": [{"price": "100.5"}, {"price": "101.0"}]},
    }
    mock_post.return_value.__aenter__.return_value = mock_response

    rate = await service.fetch_rapira_rate(sample_rapira_config)

    assert rate == 100.5


@pytest.mark.asyncio
@patch("aiohttp.ClientSession.post")
async def test_fetch_rapira_rate_non_200_returns_none(mock_post, service, sample_rapira_config):
    mock_response = AsyncMock()
    mock_response.status = 500
    mock_post.return_value.__aenter__.return_value = mock_response

    rate = await service.fetch_rapira_rate(sample_rapira_config)
    assert rate is None


@pytest.mark.asyncio
@patch("aiohttp.ClientSession.post")
async def test_fetch_rapira_rate_missing_side_returns_none(mock_post, service, sample_rapira_config):
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.json.return_value = {"ask": {"items": [{"price": "1"}]}}  # bid missing
    mock_post.return_value.__aenter__.return_value = mock_response

    rate = await service.fetch_rapira_rate(sample_rapira_config)
    assert rate is None


@pytest.mark.asyncio
@patch("aiohttp.ClientSession.post")
async def test_fetch_rapira_rate_not_enough_items_returns_none(mock_post, service, sample_rapira_config):
    """Position=2 but only one item available → None, not IndexError."""
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.json.return_value = {
        "bid": {"items": [{"price": "98.0"}]},
        "ask": {"items": [{"price": "99.0"}]},
    }
    mock_post.return_value.__aenter__.return_value = mock_response

    rate = await service.fetch_rapira_rate(sample_rapira_config)
    assert rate is None


@pytest.mark.asyncio
@patch("aiohttp.ClientSession.post")
async def test_fetch_rapira_rate_exception_returns_none(mock_post, service, sample_rapira_config):
    mock_post.side_effect = Exception("network down")

    rate = await service.fetch_rapira_rate(sample_rapira_config)
    assert rate is None


@pytest.mark.asyncio
@patch.object(RateService, "fetch_rapira_rate")
async def test_update_rate_routes_to_rapira_for_rapira_source(mock_fetch, service, mock_repository, sample_rapira_config):
    """`update_rate` dispatches based on `config.source`."""
    mock_repository.get.return_value = sample_rapira_config
    mock_fetch.return_value = 97.5

    new_rate = await service.update_rate(2)

    assert new_rate == 97.5
    mock_fetch.assert_called_once_with(sample_rapira_config)
