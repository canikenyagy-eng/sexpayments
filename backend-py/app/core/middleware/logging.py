import time
from typing import Callable

from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.logging import get_logger

logger = get_logger(__name__)


class LoggingMiddleware:
    """Pure-ASGI logging middleware.

    Avoids BaseHTTPMiddleware which wraps call_next in an anyio TaskGroup,
    causing ExceptionGroup to be raised instead of the original exception
    when a route handler fails.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        start_time = time.perf_counter()

        logger.info(
            "Request started",
            method=request.method,
            url=str(request.url),
            client_host=request.client.host if request.client else None,
        )

        status_code = 500
        original_send: Callable = send

        async def send_wrapper(message: dict) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await original_send(message)

        try:
            await self.app(scope, receive, send_wrapper)
            process_time = time.perf_counter() - start_time
            logger.info(
                "Request completed",
                method=request.method,
                url=str(request.url),
                status_code=status_code,
                duration_ms=round(process_time * 1000, 2),
            )
        except Exception as e:
            process_time = time.perf_counter() - start_time
            logger.exception(
                "Request failed",
                method=request.method,
                url=str(request.url),
                duration_ms=round(process_time * 1000, 2),
                error=str(e),
            )
            raise
