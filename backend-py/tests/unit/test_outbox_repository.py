import pytest
from unittest.mock import AsyncMock, MagicMock
from sqlalchemy.ext.asyncio import AsyncSession
from app.infrastructure.outbox.repository import OutboxRepository
from app.infrastructure.outbox.models import OutboxEvent

@pytest.fixture
def mock_session():
    session = AsyncMock(spec=AsyncSession)
    return session

@pytest.fixture
def outbox_repo(mock_session):
    return OutboxRepository(mock_session)

@pytest.mark.asyncio
async def test_get_unprocessed_events(outbox_repo, mock_session):
    # Mock the result of session.execute
    mock_result = MagicMock()
    
    # Mock the scalars().all() chain
    mock_scalars = MagicMock()
    mock_events = [OutboxEvent(id=1, event_type="test_event"), OutboxEvent(id=2, event_type="test_event_2")]
    mock_scalars.all.return_value = mock_events
    mock_result.scalars.return_value = mock_scalars
    
    mock_session.execute.return_value = mock_result
    
    # Call the method
    events = await outbox_repo.get_unprocessed_events(limit=10)
    
    # Assertions
    assert len(events) == 2
    assert events[0].id == 1
    assert events[1].id == 2
    
    mock_session.execute.assert_called_once()
    
    # We can inspect the query string if needed, but checking if execute was called is a good start
    call_args = mock_session.execute.call_args[0][0]
    assert "outbox_events" in str(call_args) # Checking if the query targets the correct table
