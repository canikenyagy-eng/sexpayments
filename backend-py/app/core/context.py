from contextvars import ContextVar
from typing import Any, Dict, List, Optional

payin_snapshot_var: ContextVar[Optional[Dict[str, Any]]] = ContextVar(
    "payin_snapshot", default=None
)

provider_requests_var: ContextVar[Optional[List[Any]]] = ContextVar(
    "provider_requests", default=None
)
