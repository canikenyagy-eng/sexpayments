from fastapi import APIRouter

from app.api.bot.v1.endpoints import dispute, merchants, notify, orders, state, support, trader

bot_router = APIRouter()

bot_router.include_router(merchants.router, prefix="/merchants", tags=["bot-merchants"])
bot_router.include_router(orders.router, prefix="/merchants/{merchant_id}/orders", tags=["bot-orders"])
bot_router.include_router(state.router, prefix="/state", tags=["bot-state"])
bot_router.include_router(support.router, prefix="/support", tags=["bot-support"])
bot_router.include_router(dispute.router, prefix="/dispute", tags=["bot-dispute"])
bot_router.include_router(notify.router, prefix="/notify", tags=["bot-notify"])
bot_router.include_router(trader.router, prefix="/trader", tags=["bot-trader"])
