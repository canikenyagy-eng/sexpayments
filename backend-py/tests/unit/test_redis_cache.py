import pytest
from unittest.mock import AsyncMock, MagicMock
from app.infrastructure.cache.redis import RedisCache

@pytest.fixture
def mock_redis_client():
    client = AsyncMock()
    return client

@pytest.fixture
def redis_cache(mock_redis_client):
    return RedisCache(mock_redis_client)

@pytest.mark.asyncio
async def test_redis_get_found(redis_cache, mock_redis_client):
    mock_redis_client.get.return_value = "cached_value"
    
    result = await redis_cache.get("my_key")
    
    assert result == "cached_value"
    mock_redis_client.get.assert_called_once_with("my_key")

@pytest.mark.asyncio
async def test_redis_get_not_found(redis_cache, mock_redis_client):
    mock_redis_client.get.return_value = None
    
    result = await redis_cache.get("missing_key")
    
    assert result is None
    mock_redis_client.get.assert_called_once_with("missing_key")

@pytest.mark.asyncio
async def test_redis_set(redis_cache, mock_redis_client):
    await redis_cache.set("my_key", "my_value", ttl=3600)
    
    mock_redis_client.set.assert_called_once_with("my_key", "my_value", ex=3600)

@pytest.mark.asyncio
async def test_redis_delete(redis_cache, mock_redis_client):
    await redis_cache.delete("my_key")
    
    mock_redis_client.delete.assert_called_once_with("my_key")
