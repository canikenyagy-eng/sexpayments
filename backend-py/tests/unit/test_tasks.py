import pytest
from unittest.mock import MagicMock, patch
from celery.exceptions import Retry

from app.workers.tasks.orders import assign_requisite_task
from app.workers.tasks.callbacks import send_order_callback

# --- Orders Task Tests ---

def test_assign_requisite_task_success():
    def mock_run(coro):
        coro.close()
        
    with patch("app.workers.tasks.orders.asyncio.get_event_loop") as mock_get_loop, \
         patch("app.workers.tasks.orders.asyncio.run", side_effect=mock_run) as mock_asyncio_run, \
         patch("app.workers.tasks.orders._assign_requisite_async") as mock_async_func, \
         patch("celery.app.task.Task.retry") as mock_retry:
        
        mock_loop = MagicMock()
        mock_loop.is_running.return_value = False
        mock_get_loop.return_value = mock_loop
        
        # Call the task's .run method without passing mock_self
        assign_requisite_task.run(123)
        
        mock_asyncio_run.assert_called_once()
        mock_retry.assert_not_called()

def test_assign_requisite_task_retry_on_exception():
    def mock_run_exc(coro):
        coro.close()
        raise Exception("DB Error")

    with patch("app.workers.tasks.orders.asyncio.get_event_loop") as mock_get_loop, \
         patch("app.workers.tasks.orders.asyncio.run", side_effect=mock_run_exc), \
         patch("app.workers.tasks.orders._assign_requisite_async"), \
         patch("celery.app.task.Task.retry", side_effect=Retry("Retry triggered")) as mock_retry:
        
        mock_loop = MagicMock()
        mock_loop.is_running.return_value = False
        mock_get_loop.return_value = mock_loop
        
        with pytest.raises(Retry):
            assign_requisite_task.run(123)
            
        mock_retry.assert_called_once()
        assert "countdown" in mock_retry.call_args[1]

# --- Callbacks Task Tests ---

def test_send_order_callback_success():
    def mock_run(coro):
        coro.close()

    with patch("app.workers.tasks.callbacks.asyncio.run", side_effect=mock_run) as mock_asyncio_run, \
         patch("celery.app.task.Task.retry") as mock_retry:
        
        send_order_callback.run(456)
        
        mock_asyncio_run.assert_called_once()
        mock_retry.assert_not_called()

def test_send_order_callback_retry_on_exception():
    def mock_run_exc(coro):
        coro.close()
        raise Exception("Network Error")

    with patch("app.workers.tasks.callbacks.asyncio.run", side_effect=mock_run_exc), \
         patch("celery.app.task.Task.retry", side_effect=Retry("Retry triggered")) as mock_retry:
        
        with pytest.raises(Retry):
            send_order_callback.run(456)
            
        mock_retry.assert_called_once()
        assert "exc" in mock_retry.call_args[1]
