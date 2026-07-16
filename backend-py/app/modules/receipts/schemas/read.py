"""Read schema for receipts.

A single minimal item shape — uuid / created_at / source / filename — shared by
trader, admin and merchant list responses. Carries nothing sensitive (no
provider, no internal ids), so it's safe across roles. The file is fetched
separately by ``uuid`` from the download endpoint.
"""
import os
from datetime import datetime

from pydantic import BaseModel


class ReceiptItem(BaseModel):
    uuid: str
    created_at: datetime
    source: str
    filename: str

    @classmethod
    def from_model(cls, r) -> "ReceiptItem":
        return cls(
            uuid=str(r.uuid),
            created_at=r.created_at,
            source=r.source.value if hasattr(r.source, "value") else str(r.source),
            filename=os.path.basename(r.file_path or ""),
        )
