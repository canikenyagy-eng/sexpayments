"""Receipt-check provider adapters.

Each adapter implements `ReceiptCheckProviderClient`. The factory
`build_client_for_provider(provider)` returns the right concrete
implementation based on `provider.adapter_type`. New providers slot in
without touching the service layer.
"""
from app.common.enums.receipt_checks import ReceiptCheckProviderAdapter
from app.modules.receipt_checks.models import ReceiptCheckProvider
from app.modules.receipt_checks.providers.base import ReceiptCheckProviderClient
from app.modules.receipt_checks.providers.detectio import DetectioClient
from app.modules.receipt_checks.providers.trexo import TrexoClient


def build_client_for_provider(provider: ReceiptCheckProvider) -> ReceiptCheckProviderClient:
    adapter = provider.adapter_type
    if adapter == ReceiptCheckProviderAdapter.TREXO.value or adapter == ReceiptCheckProviderAdapter.TREXO:
        return TrexoClient(provider)
    if adapter == ReceiptCheckProviderAdapter.DETECTIO.value or adapter == ReceiptCheckProviderAdapter.DETECTIO:
        return DetectioClient(provider)
    raise ValueError(f"Unknown receipt-check adapter: {adapter}")


__all__ = [
    "ReceiptCheckProviderClient",
    "TrexoClient",
    "DetectioClient",
    "build_client_for_provider",
]
