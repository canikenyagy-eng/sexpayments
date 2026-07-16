"""``clients.public_id`` (our internal, merchant-opaque client id) is auto-assigned
on ORM insert (app-side ``uuid4`` default) and unique per client; the merchant
``unique_clients_enabled`` toggle defaults OFF. Real in-memory session.
"""
from __future__ import annotations

import uuid

import pytest

from app.common.enums.finances import Currency
from app.modules.clients.models import Client
from app.modules.merchants.models import Merchant


@pytest.mark.asyncio
async def test_public_id_autoassigned_and_unique(session):
    # Explicit ids: clients.id is a BigInteger PK, which SQLite (unlike a plain
    # INTEGER PK) doesn't autoincrement — irrelevant to public_id, which is what
    # we're asserting (it's auto-filled by the model's uuid4 default).
    a = Client(id=1, merchant_id=1, client_user_id="cli-a")
    b = Client(id=2, merchant_id=1, client_user_id="cli-b")
    session.add_all([a, b])
    await session.flush()

    assert isinstance(a.public_id, uuid.UUID)
    assert isinstance(b.public_id, uuid.UUID)
    assert a.public_id != b.public_id  # distinct per client


@pytest.mark.asyncio
async def test_merchant_unique_clients_defaults_off(session):
    m = Merchant(user_id=1, api_key="k-uc", api_secret="s-uc", currency=Currency.RUB, fees={})
    session.add(m)
    await session.flush()

    assert m.unique_clients_enabled is False
