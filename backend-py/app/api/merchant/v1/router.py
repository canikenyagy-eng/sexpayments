from fastapi import APIRouter

from app.api.merchant.v1.endpoints import orders, payments, rates, profile, callbacks, disputes

merchant_router = APIRouter()

merchant_router.include_router(profile.router, prefix="/profile", tags=["merchant-profile"])
merchant_router.include_router(orders.router, prefix="/orders", tags=["merchant-orders"])
merchant_router.include_router(payments.router, prefix="/payments", tags=["merchant-payments"])
merchant_router.include_router(rates.router, prefix="/rates", tags=["merchant-rates"])
merchant_router.include_router(callbacks.router, prefix="/callbacks", tags=["merchant-callbacks"])
merchant_router.include_router(disputes.router, prefix="/disputes", tags=["merchant-disputes"])
