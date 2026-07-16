from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict


def _datetime_to_utc_iso(v: datetime) -> str:
    """Serialize datetime as ISO string with explicit UTC offset.

    Naive datetimes are assumed to be UTC (matches `app.common.types.utcnow`).
    """
    if v.tzinfo is None:
        v = v.replace(tzinfo=timezone.utc)
    return v.isoformat()


class BaseSchema(BaseModel):
    """Base schema for all Pydantic models."""

    model_config = ConfigDict(
        populate_by_name=True,
        str_strip_whitespace=True,
    )


class BaseResponseSchema(BaseSchema):
    """Base schema for all response models. Enables ORM mode.

    Datetime fields are serialized with explicit UTC offset so that frontend
    consumers (`new Date(value)`, `dayjs(value)`) can correctly convert to
    the user's local timezone instead of misreading naive strings as local.
    """

    model_config = ConfigDict(
        from_attributes=True,
        json_encoders={datetime: _datetime_to_utc_iso},
    )
