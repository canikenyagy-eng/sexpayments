from decimal import Decimal
from app.common.enums.pooling import PoolingStrategy
from app.modules.requisites.models import Requisite
from app.modules.traders.models import Trader


def test_weighted_strategy_member():
    assert PoolingStrategy.WEIGHTED.value == "weighted"


def test_priority_columns_exist_with_defaults():
    cols = Requisite.__table__.c
    assert "trader_priority" in cols and "priority_score" in cols
    assert cols["trader_priority"].default.arg == 1
    assert Decimal(str(cols["priority_score"].default.arg)) == Decimal("100")
    assert "priority_bonus_percent" in Trader.__table__.c
