"""Disputes schemas package.

Same migration pattern as orders/schemas — admin.py holds the full-surface
schemas (re-exported here for backwards-compat), and merchant.py / trader.py
declare the slimmer, role-restricted variants.
"""
from app.modules.disputes.schemas.admin import (  # noqa: F401
    DisputeBase,
    DisputeCreate,
    DisputeResolutionRequest,
    DisputeResponse,
)
from app.modules.disputes.schemas.merchant import (  # noqa: F401
    DisputeMerchantResponse,
)
from app.modules.disputes.schemas.trader import (  # noqa: F401
    DisputeTraderResponse,
)
