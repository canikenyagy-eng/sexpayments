import uuid
from contextvars import ContextVar

import structlog
from starlette.types import ASGIApp, Receive, Scope, Send

request_id_context_var: ContextVar[str] = ContextVar("request_id", default="")


class RequestIdMiddleware:
    """Pure-ASGI middleware that assigns a unique request ID to every HTTP request.

    Avoids BaseHTTPMiddleware which wraps the handler in an anyio TaskGroup,
    causing ExceptionGroup instead of the original exception and preventing
    ContextVar propagation.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        raw_headers = dict(scope.get("headers", []))
        request_id = raw_headers.get(b"x-request-id", b"").decode("latin-1") or str(uuid.uuid4())

        scope.setdefault("state", {})
        if isinstance(scope["state"], dict):
            scope["state"]["request_id"] = request_id
        else:
            scope["state"].request_id = request_id

        request_id_context_var.set(request_id)
        structlog.contextvars.bind_contextvars(request_id=request_id)

        async def send_wrapper(message: dict) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode("latin-1")))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)
