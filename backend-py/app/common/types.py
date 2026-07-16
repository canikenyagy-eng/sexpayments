from datetime import datetime, timezone
from typing import Annotated, Optional

from pydantic import BeforeValidator, PlainSerializer


def _unix_to_naive_utc(v: object) -> Optional[datetime]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.replace(tzinfo=None) if v.tzinfo else v
    if isinstance(v, str):
        try:
            v = float(v)
        except ValueError:
            raise ValueError(f"Expected Unix timestamp, got non-numeric string: {v!r}")
    if not isinstance(v, (int, float)):
        raise ValueError(f"Expected Unix timestamp (int/float/str), got {type(v).__name__}")
    return datetime.fromtimestamp(float(v), tz=timezone.utc).replace(tzinfo=None)


UnixTimestamp = Annotated[Optional[datetime], BeforeValidator(_unix_to_naive_utc)]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _to_utc_iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


UTCDateTime = Annotated[
    datetime,
    PlainSerializer(_to_utc_iso, return_type=str, when_used="json"),
]
