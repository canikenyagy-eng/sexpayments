"""Create default admin user and initial payment options if none exists."""

import asyncio
import json
import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.core.config import get_settings
from app.core.security import get_password_hash
from app.infrastructure.db.session import SessionLocal
from app.modules.users.models import User
from app.modules.payments.models import PaymentOption
from app.common.enums.finances import Currency

logger = logging.getLogger(__name__)

PAYMENT_OPTIONS_FILE = (
    Path(__file__).resolve().parents[2] / "modules" / "payments" / "options.json"
)


async def _seed_admin(session):
    settings = get_settings()
    exists = (await session.execute(
        select(User).where(User.role == "admin").limit(1)
    )).scalar_one_or_none()

    if exists:
        logger.info("Admin user already exists (id=%s), skipping admin seed", exists.id)
        return

    user = User(
        username=settings.ADMIN_USERNAME,
        password=get_password_hash(settings.ADMIN_PASSWORD),
        role="admin",
        totp_enabled=False,
        is_blocked=False,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    logger.info("Admin user created: id=%s, username=%s", user.id, user.username)


def _load_payment_options() -> list[dict]:
    """Load the payment options catalog from the on-disk JSON file.

    The file is the single source of truth for the seed: keep it sorted and
    add new banks there rather than touching this module.
    """
    if not PAYMENT_OPTIONS_FILE.is_file():
        logger.warning(
            "Payment options file not found: %s — skipping seed", PAYMENT_OPTIONS_FILE
        )
        return []

    with PAYMENT_OPTIONS_FILE.open(encoding="utf-8") as f:
        raw = json.load(f)

    if not isinstance(raw, list):
        raise ValueError(
            f"{PAYMENT_OPTIONS_FILE} must contain a JSON array of payment options"
        )

    required = {"code", "name", "currency", "is_active", "supported_methods"}
    options: list[dict] = []
    for idx, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ValueError(f"Payment option #{idx} is not an object: {entry!r}")
        missing = required - entry.keys()
        if missing:
            raise ValueError(
                f"Payment option #{idx} ({entry.get('code')!r}) "
                f"is missing required keys: {sorted(missing)}"
            )
        try:
            currency = Currency(entry["currency"])
        except ValueError as exc:
            raise ValueError(
                f"Payment option {entry['code']!r} has unknown currency "
                f"{entry['currency']!r}"
            ) from exc

        methods = entry["supported_methods"]
        if not isinstance(methods, list) or not methods:
            raise ValueError(
                f"Payment option {entry['code']!r} has invalid supported_methods: "
                f"{methods!r}"
            )

        options.append(
            {
                "code": entry["code"],
                "name": entry["name"],
                "logo_url": entry.get("logo_url"),
                "supported_methods": methods,
                "currency": currency,
                "is_active": bool(entry["is_active"]),
            }
        )
    return options


async def _seed_payment_options(session):
    """Idempotently insert payment options from the JSON catalog.

    Uses `INSERT ... ON CONFLICT (code) DO NOTHING`, so existing rows are
    never updated — operators can safely tweak names/logos/flags in the DB
    without the next pipeline overwriting them. To roll out catalog changes,
    update the row in the DB explicitly.
    """
    options = _load_payment_options()
    if not options:
        logger.info("No payment options to seed")
        return

    inserted = 0
    for opt in options:
        stmt = (
            insert(PaymentOption)
            .values(**opt)
            .on_conflict_do_nothing(index_elements=["code"])
            .returning(PaymentOption.id)
        )
        result = await session.execute(stmt)
        if result.scalar_one_or_none() is not None:
            inserted += 1

    await session.commit()
    if inserted:
        logger.info(
            "Seeded %s new payment option(s) (catalog size: %s)",
            inserted,
            len(options),
        )
    else:
        logger.info(
            "All %s payment option(s) from catalog already exist, nothing to seed",
            len(options),
        )


async def _seed():
    async with SessionLocal() as session:
        await _seed_admin(session)
        await _seed_payment_options(session)


def run_seed():
    asyncio.run(_seed())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_seed()
