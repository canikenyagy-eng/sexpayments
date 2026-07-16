"""Orders schemas package.

We're transitioning to per-role schema files (admin.py, trader.py, ...)
so different audiences see different field surfaces. Until the migration
is done, ``admin.py`` holds the "full" schemas previously in
``schemas.py`` — re-exported here so the existing imports keep working:

    from app.modules.orders.schemas import OrderResponse

Trader-facing endpoints should import from ``trader.py`` directly:

    from app.modules.orders.schemas.trader import OrderTraderResponse
"""
from app.modules.orders.schemas.admin import (  # noqa: F401
    AdminOrderResponse,
    AdminOrderUpdate,
    CallbackAttemptResponse,
    FailOrderRequest,
    LedgerEntryResponse,
    MerchantOrderResponse,
    MerchantPayinCreate,
    MerchantRequisiteInfo,
    OrderBase,
    OrderCreate,
    OrderDebugResponse,
    OrderResponse,
    OrderStatusHistoryResponse,
    PaginatedAdminOrderResponse,
    PaginatedOrderResponse,
    RequisiteInfo,
    TraderCandidateInfo,
)
from app.modules.orders.schemas.trader import (  # noqa: F401
    OrderTraderResponse,
    PaginatedTraderOrderResponse,
    RequisiteTraderInfo,
)
