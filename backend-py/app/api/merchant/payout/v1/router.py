from fastapi import APIRouter

from app.api.merchant.payout.v1.endpoints import payouts

payout_merchant_router = APIRouter()

payout_merchant_router.include_router(payouts.router, prefix="/payouts", tags=["payout-merchant"])
