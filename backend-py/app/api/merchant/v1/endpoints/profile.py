from fastapi import APIRouter, Depends, status

from app.api.dependencies import get_service
from app.api.merchant.dependencies import get_current_merchant
from app.common.enums.payments import PaymentMethod
from app.common.enums.finances import Currency
from app.modules.finance.service import FinanceService
from app.modules.merchants.models import Merchant
from app.modules.merchants.schemas import MerchantProfileResponse, PaymentMethodInfo
from app.modules.payments.service import PaymentOptionService

router = APIRouter()

# GET /me Get merchant profile

@router.get(
    "/me",
    response_model=MerchantProfileResponse,
    status_code=status.HTTP_200_OK,
    summary="Get merchant profile",
    description="Get current merchant information including balance, fee rates, and active payment methods",
)
async def get_merchant_profile(
    merchant: Merchant = Depends(get_current_merchant),
    finance_service: FinanceService = Depends(get_service(FinanceService)),
    payment_service: PaymentOptionService = Depends(get_service(PaymentOptionService)),
):
    # Always read the merchant-scoped USDT balance: every credit/debit path in
    # `FinanceService` writes to `merchant_id` in USDT and ignores
    # `users.use_shared_balance`, so the old conditional branch could return 0
    # while the real money sat on `merchant_id`.
    balance = await finance_service.get_or_create_merchant_balance(
        merchant=merchant, currency=Currency.USDT
    )
    
    # Get all active payment options
    active_options = await payment_service.get_active_options()
    
    # Group options by payment method
    methods_map = {method: [] for method in PaymentMethod}
    for option in active_options:
        for method_str in option.supported_methods:
            try:
                method_enum = PaymentMethod(method_str)
                methods_map[method_enum].append(option)
            except ValueError:
                pass  # Ignore unknown methods
                
    # Build payment methods info array
    payment_methods_info = []
    merchant_fees = merchant.fees or {}
    
    for method, options in methods_map.items():
        if options:  # Only include methods that have at least one active option
            fee = float(merchant_fees.get(method.value, 0.0))
            payment_methods_info.append(
                PaymentMethodInfo(
                    method=method,
                    fee_percentage=fee
                )
            )
    
    return MerchantProfileResponse(
        id=merchant.id,
        status=merchant.status,
        currency=merchant.currency,
        webhook_url=merchant.webhook_url,
        balance=float(balance.amount),
        payment_methods=payment_methods_info
    )
