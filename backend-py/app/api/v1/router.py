from fastapi import APIRouter

from app.api.v1.endpoints import audit, auth, users, rates, traders, requisites, callbacks, teamleaders, merchants, disputes, orders, finances, stats, payments, cascade, platform_settings, receipt_moderations, selectors, payouts, doliv, clients, broadcast

api_router = APIRouter()

api_router.include_router(stats.router, prefix="/stats", tags=["stats"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(traders.router, prefix="/traders", tags=["traders"])
api_router.include_router(requisites.router, prefix="/requisites", tags=["requisites"])
api_router.include_router(audit.router, prefix="/audit", tags=["audit"])
api_router.include_router(rates.router, prefix="/rates", tags=["rates"])
api_router.include_router(callbacks.router, prefix="/callbacks", tags=["callbacks"])
api_router.include_router(teamleaders.router, prefix="/teamleaders", tags=["teamleaders"])
api_router.include_router(merchants.router, prefix="/merchants", tags=["merchants"])
api_router.include_router(disputes.router, prefix="/disputes", tags=["disputes"])
api_router.include_router(orders.router, prefix="/orders", tags=["orders"])
api_router.include_router(finances.router, prefix="/finances", tags=["finances"])
api_router.include_router(payments.router, prefix="/payments", tags=["payments"])
api_router.include_router(cascade.router, prefix="/cascade", tags=["cascade"])
api_router.include_router(platform_settings.router, prefix="/platform-settings", tags=["platform-settings"])
api_router.include_router(receipt_moderations.router, prefix="/receipt-moderations", tags=["receipt-moderations"])
api_router.include_router(selectors.router, prefix="/selectors", tags=["selectors"])
api_router.include_router(payouts.router, prefix="/payouts", tags=["payouts"])
api_router.include_router(doliv.router, prefix="/doliv", tags=["doliv"])
api_router.include_router(clients.router, prefix="/clients", tags=["clients"])
api_router.include_router(broadcast.router, prefix="/broadcast", tags=["broadcast"])
