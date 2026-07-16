import aiohttp
import logging
from datetime import datetime
from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums.rates import OrderBookSide, RateSource
from app.common.types import utcnow
from app.core.exceptions import NotFoundException
from app.modules.base.service import BaseService
from app.modules.rates.models import RateConfig
from app.modules.rates.repository import RateConfigRepository
from app.modules.rates.schemas import RateConfigCreate, RateConfigUpdate

logger = logging.getLogger(__name__)


class RateService(BaseService):
    def __init__(self, session: AsyncSession):
        super().__init__(session)
        self.repository = RateConfigRepository(session)

    async def create_config(self, data: RateConfigCreate, admin_user_id: Optional[int] = None) -> RateConfig:
        async with self.session.begin_nested():
            config = await self.repository.create(data.model_dump())
            
            await self.audit_log(
                action="create_rate_config",
                entity_type="rate_config",
                entity_id=config.id,
                user_id=admin_user_id,
                new_values=data.model_dump(),
            )
            
        return config

    async def update_config(self, config_id: int, data: RateConfigUpdate, admin_user_id: Optional[int] = None) -> RateConfig:
        config = await self.repository.get(config_id)
        if not config:
            raise NotFoundException(f"Rate config {config_id} not found")

        update_data = data.model_dump(exclude_unset=True)
        if update_data:
            old_values = {k: getattr(config, k) for k in update_data.keys()}
            
            async with self.session.begin_nested():
                config = await self.repository.update(config_id, update_data)
                
                await self.audit_log(
                    action="update_rate_config",
                    entity_type="rate_config",
                    entity_id=config_id,
                    user_id=admin_user_id,
                    old_values=old_values,
                    new_values=update_data,
                )
                
        return config

    async def delete_config(self, config_id: int, admin_user_id: Optional[int] = None) -> None:
        config = await self.repository.get(config_id)
        if not config:
            raise NotFoundException(f"Rate config {config_id} not found")
        
        old_values = {
            "crypto_currency": config.crypto_currency,
            "fiat_currency": config.fiat_currency.value,
            "source": config.source.value,
            "is_active": config.is_active,
        }
        
        async with self.session.begin_nested():
            await self.repository.delete(config_id)
            
            await self.audit_log(
                action="delete_rate_config",
                entity_type="rate_config",
                entity_id=config_id,
                user_id=admin_user_id,
                old_values=old_values,
            )

    async def get_config(self, config_id: int) -> RateConfig:
        config = await self.repository.get(config_id)
        if not config:
            raise NotFoundException(f"Rate config {config_id} not found")
        return config

    async def list_configs(self) -> List[RateConfig]:
        return await self.repository.get_all()

    async def get_active_configs(self) -> List[RateConfig]:
        return await self.repository.get_active_configs()

    async def fetch_bybit_rate(self, config: RateConfig) -> Optional[float]:
        """
        Fetch rate from Bybit P2P API based on config settings.
        """
        url = "https://api2.bybit.com/fiat/otc/item/online"
        
        # Bybit side: "1" for BUY (green), "0" for SELL (red)
        side_val = "1" if config.side == OrderBookSide.BUY else "0"
        
        payload = {
            "userId": "",
            "tokenId": config.crypto_currency,
            "currencyId": config.fiat_currency.value,
            "payment": config.payment_methods,
            "side": side_val,
            "size": str(max(10, config.position + 5)),
            "page": "1",
            "amount": "",
            "authMaker": False,
            "canTrade": False
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=10) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data.get("ret_code") == 0 and data.get("result", {}).get("items"):
                            items = data["result"]["items"]
                            # Ensure we have enough items for the requested position
                            if len(items) >= config.position:
                                # Position is 1-indexed, so we subtract 1 for the array index
                                target_item = items[config.position - 1]
                                return float(target_item["price"])
            return None
        except Exception as e:
            logger.error(f"Error fetching Bybit rate for config {config.id}: {e}")
            return None

    async def fetch_rapira_rate(self, config: RateConfig) -> Optional[float]:
        """
        Fetch rate from Rapira exchange orderbook via POST /market/exchange-plate-mini.

        BUY side (green / bid) — buyers of the crypto asset.
        SELL side (red / ask) — sellers of the crypto asset.
        Symbol is constructed as '{crypto_currency}/{fiat_currency}', e.g. 'USDT/RUB'.
        """
        url = "https://api.rapira.net/market/exchange-plate-mini"
        symbol = f"{config.crypto_currency}/{config.fiat_currency.value}"

        try:
            async with aiohttp.ClientSession() as session:
                form = aiohttp.FormData()
                form.add_field("symbol", symbol)
                async with session.post(url, data=form, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    if response.status != 200:
                        logger.error(
                            "Rapira market depth returned non-200 status for config %s: %s",
                            config.id, response.status,
                        )
                        return None

                    data = await response.json(content_type=None)
                    if not data:
                        logger.error("Rapira returned empty response for config %s (symbol %s)", config.id, symbol)
                        return None

                    side_key = "bid" if config.side == OrderBookSide.BUY else "ask"
                    side_data = data.get(side_key)
                    if not side_data:
                        logger.error(
                            "Rapira response missing '%s' side for config %s (symbol %s)",
                            side_key, config.id, symbol,
                        )
                        return None

                    items = side_data.get("items", [])
                    if len(items) < config.position:
                        logger.warning(
                            "Rapira: not enough items (%d) for position %d (config %s)",
                            len(items), config.position, config.id,
                        )
                        return None

                    return float(items[config.position - 1]["price"])

        except Exception as e:
            logger.error("Error fetching Rapira rate for config %s: %s", config.id, e)
            return None

    async def update_rate(self, config_id: int) -> Optional[float]:
        """
        Fetch the latest rate and update the config in the database.
        """
        config = await self.repository.get(config_id)
        if not config:
            logger.error(f"RateConfig with id {config_id} not found")
            raise NotFoundException(f"RateConfig with id {config_id} not found")

        if not config.is_active:
            logger.warning(f"RateConfig {config_id} is not active, skipping rate update")
            return None

        new_rate = None
        if config.source == RateSource.BYBIT:
            new_rate = await self.fetch_bybit_rate(config)
        elif config.source == RateSource.RAPIRA:
            new_rate = await self.fetch_rapira_rate(config)

        if new_rate is not None:
            async with self.session.begin_nested():
                await self.repository.update(
                    config_id,
                    {
                        "current_rate": new_rate,
                        "last_updated_at": utcnow()
                    }
                )
            return new_rate
        
        return config.current_rate
