"""Unit tests for OrderRepository.list_orders — the generic, role-agnostic
query. We compile the statement to SQL (no DB) and assert the per-scope search
shape, so a regression in the search predicates is caught here.
"""
import pytest
from sqlalchemy.dialects import postgresql

# Load related mappers so Order's string-based relationships resolve under
# test isolation (Requisite / PaymentOption / Merchant / User).
import app.modules.requisites.models  # noqa: F401
import app.modules.payments.models  # noqa: F401
import app.modules.merchants.models  # noqa: F401
import app.modules.users.models  # noqa: F401

from app.modules.orders.repository import OrderRepository


class _FakeResult:
    def scalars(self):
        class _S:
            def all(self_inner):
                return []
        return _S()


class _CapturingSession:
    """Captures the compiled SQL of the last executed statement."""
    def __init__(self):
        self.sql = None

    async def execute(self, stmt):
        self.sql = str(
            stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
        ).lower()
        return _FakeResult()


async def _search_sql(scope: str, term: str = "abc") -> str:
    session = _CapturingSession()
    await OrderRepository(session).list_orders(search=term, search_scope=scope)
    return session.sql


# Search *predicate* markers (the column appears in the SELECT list regardless,
# so we assert on the ilike condition, not the bare column name).
# literal_binds escapes % → %% in the rendered SQL.
_PROVIDER_PRED = "orders.provider_order_id ilike '%%abc%%'"
_EXTERNAL_PRED = "orders.external_id ilike '%%abc%%'"


@pytest.mark.asyncio
async def test_admin_search_includes_provider_order_id():
    sql = await _search_sql("admin")
    assert _EXTERNAL_PRED in sql
    assert "cast(orders.uuid as varchar) ilike '%%abc%%'" in sql
    # Admins can also search by the cascade provider's order id.
    assert _PROVIDER_PRED in sql


@pytest.mark.asyncio
async def test_merchant_search_excludes_provider_order_id():
    """provider_order_id is admin-only — never searched (nor exposed) for
    merchant/trader, to avoid leaking cascade routing."""
    sql = await _search_sql("merchant")
    assert _EXTERNAL_PRED in sql
    assert _PROVIDER_PRED not in sql


@pytest.mark.asyncio
async def test_trader_search_excludes_provider_order_id():
    sql = await _search_sql("trader")
    assert _PROVIDER_PRED not in sql
    # but still joins requisite fields
    assert "requisites" in sql
