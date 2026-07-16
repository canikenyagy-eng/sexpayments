import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.modules.audit.service import AuditService
from app.modules.audit.models import AuditLog
from app.modules.audit.schemas import AuditLogCreate
from app.core.middleware.request_id import request_id_context_var

class AsyncContextManagerMock:
    async def __aenter__(self):
        return self
    async def __aexit__(self, exc_type, exc, tb):
        pass

@pytest.fixture
def mock_session():
    session = MagicMock()
    session.begin.return_value = AsyncContextManagerMock()
    session.execute = AsyncMock()
    session.get = AsyncMock()
    return session

@pytest.fixture
def service(mock_session):
    svc = AuditService(mock_session)
    svc.repository = AsyncMock()
    return svc

@pytest.mark.asyncio
async def test_log_action_with_explicit_request_id(service):
    mock_log = MagicMock(spec=AuditLog)
    service.repository.create.return_value = mock_log
    
    result = await service.log_action(
        action="update",
        entity_type="user",
        entity_id="123",
        user_id="42",
        old_values={"status": "active"},
        new_values={"status": "inactive"},
        request_id="req-123"
    )
    
    assert result == mock_log
    service.repository.create.assert_called_once()
    
    create_args = service.repository.create.call_args[0][0]
    assert create_args["action"] == "update"
    assert create_args["entity_type"] == "user"
    assert create_args["entity_id"] == "123"
    assert create_args["user_id"] == "42"
    assert create_args["old_values"] == {"status": "active"}
    assert create_args["new_values"] == {"status": "inactive"}
    assert create_args["request_id"] == "req-123"

@pytest.mark.asyncio
async def test_log_action_with_context_var_request_id(service):
    mock_log = MagicMock(spec=AuditLog)
    service.repository.create.return_value = mock_log
    
    token = request_id_context_var.set("ctx-req-456")
    
    try:
        result = await service.log_action(
            action="delete",
            entity_type="order",
            entity_id="999"
        )
        
        assert result == mock_log
        service.repository.create.assert_called_once()
        
        create_args = service.repository.create.call_args[0][0]
        assert create_args["action"] == "delete"
        assert create_args["entity_type"] == "order"
        assert create_args["entity_id"] == "999"
        assert create_args["request_id"] == "ctx-req-456"
        assert create_args["user_id"] is None
        assert create_args["old_values"] is None
        assert create_args["new_values"] is None
    finally:
        request_id_context_var.reset(token)

@pytest.mark.asyncio
async def test_get_logs_for_entity(service, mock_session):
    mock_log_1 = MagicMock(spec=AuditLog)
    mock_log_2 = MagicMock(spec=AuditLog)
    
    mock_result = MagicMock()
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = [mock_log_1, mock_log_2]
    mock_result.scalars.return_value = mock_scalars
    
    mock_session.execute.return_value = mock_result
    
    result = await service.get_logs_for_entity(
        entity_type="user",
        entity_id="123",
        skip=10,
        limit=5
    )
    
    assert len(result) == 2
    assert result == [mock_log_1, mock_log_2]
    
    mock_session.execute.assert_called_once()
    
    stmt = mock_session.execute.call_args[0][0]
    stmt_str = str(stmt)
    assert "entity_type" in stmt_str
    assert "entity_id" in stmt_str
    assert "ORDER BY" in stmt_str
    assert "created_at DESC" in stmt_str


@pytest.mark.asyncio
async def test_list_api_logs_delegates_to_clickhouse(service):
    with patch(
        "app.modules.audit.service.ch_logs.list_merchant_api_logs",
        new=AsyncMock(return_value=[]),
    ) as mock_list:
        result = await service.list_api_logs(
            skip=0, limit=10, merchant_id=5, method="POST",
            endpoint_group="orders", sort_by="created_at", sort_order="asc",
        )

    assert result == []
    mock_list.assert_called_once_with(
        skip=0, limit=10, merchant_id=5, method="POST",
        endpoint_group="orders", sort_by="created_at", sort_order="asc",
    )


@pytest.mark.asyncio
async def test_get_snapshot_by_request_id_found(service):
    snap = {"request_id": "req-1", "result": {"success": True}}
    with patch(
        "app.modules.audit.service.ch_logs.get_snapshot_by_request_id",
        new=AsyncMock(return_value=snap),
    ) as mock_get:
        result = await service.get_snapshot_by_request_id("req-1")

    assert result == snap
    mock_get.assert_called_once_with("req-1")


@pytest.mark.asyncio
async def test_get_snapshot_by_request_id_not_found(service):
    with patch(
        "app.modules.audit.service.ch_logs.get_snapshot_by_request_id",
        new=AsyncMock(return_value=None),
    ) as mock_get:
        result = await service.get_snapshot_by_request_id("missing")

    assert result is None
    mock_get.assert_called_once_with("missing")
