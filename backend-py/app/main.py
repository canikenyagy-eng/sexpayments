from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.openapi.docs import get_swagger_ui_html, get_redoc_html
from fastapi.openapi.utils import get_openapi
from fastapi.middleware.cors import CORSMiddleware
import sentry_sdk

from app.api.v1.router import api_router
from app.api.merchant.v1.router import merchant_router
from app.api.merchant.payout.v1.router import payout_merchant_router
from app.api.bot.v1.router import bot_router
from app.api.cascade.v1.router import cascade_callbacks_router
from app.core.config import get_settings
from app.core.error_handlers import setup_error_handlers
from app.core.logging import setup_logging
from app.core.middleware.logging import LoggingMiddleware
from app.core.middleware.merchant_api_logging import MerchantApiLoggingMiddleware
from app.core.middleware.request_id import RequestIdMiddleware
from app.core.health import check_readiness
from app.core.observability import setup_metrics
from app.infrastructure.cache.redis import redis_client

settings = get_settings()

# Setup logging before app creation
setup_logging(log_level="DEBUG" if settings.DEBUG else "INFO")

if settings.SENTRY_DSN:
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
    from sentry_sdk.integrations.starlette import StarletteIntegration

    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.APP_ENV,
        release=settings.SENTRY_RELEASE or None,
        traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
        profiles_sample_rate=settings.SENTRY_PROFILES_SAMPLE_RATE,
        send_default_pii=False,
        attach_stacktrace=True,
        integrations=[
            StarletteIntegration(transaction_style="endpoint"),
            FastApiIntegration(transaction_style="endpoint"),
            SqlalchemyIntegration(),
        ],
    )

@asynccontextmanager
async def lifespan(app: FastAPI):
    import logging as _logging

    _log = _logging.getLogger(__name__)

    from app.infrastructure.clickhouse.sink import aclose_sink, init_sink

    init_sink(settings.CLICKHOUSE_ENABLED)
    if not settings.CLICKHOUSE_ENABLED:
        _log.warning(
            "CLICKHOUSE_ENABLED is false — provider/audit logging and the migrated "
            "stats (payin counts/sums, conversion, 24h volume) will read as empty/0."
        )
    _ch_task = None
    if settings.CLICKHOUSE_ENABLED:
        import asyncio as _asyncio
        from pathlib import Path as _Path

        from app.infrastructure.clickhouse.client import ensure_schema

        _schema = _Path(__file__).resolve().parent / "infrastructure" / "clickhouse" / "schema"
        ddl_files = [
            _schema / "cascading.sql",   # provider_requests, provider_callbacks
            _schema / "audit.sql",        # merchant_api_logs, order_creation_snapshots
            _schema / "activity.sql",     # requisite_activity_snapshots
        ]

        async def _bootstrap_clickhouse() -> None:
            loop = _asyncio.get_running_loop()
            for attempt in range(1, 11):
                try:
                    for ddl in ddl_files:
                        await loop.run_in_executor(None, ensure_schema, ddl)
                    _log.info("ClickHouse schema ready (attempt %d)", attempt)
                    return
                except Exception as exc:  # noqa: BLE001 — never block startup on CH
                    _log.warning("ClickHouse schema bootstrap attempt %d/10 failed: %s", attempt, exc)
                    await _asyncio.sleep(3)
            _log.error("ClickHouse schema bootstrap gave up after 10 attempts; "
                       "provider logging is inert until the table exists")

        _ch_task = _asyncio.create_task(_bootstrap_clickhouse())

    yield

    if _ch_task is not None and not _ch_task.done():
        _ch_task.cancel()
    await aclose_sink()
    await redis_client.aclose()

app = FastAPI(
    title="psmini API",
    version="1.0.0",
    # The built-in public Swagger is disabled on every environment — the
    # internal API surface is admin-only, so we mount a cookie-authenticated
    # variant below (`app.api.docs.router`) that requires a valid admin JWT.
    # Public-facing docs for the merchant integration API remain open and
    # are registered separately at `/api/merchant/docs` further down.
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Custom Middlewares (order matters: first added is innermost, last added is outermost. Wait, in Starlette, last added is outermost. So RequestId should be added LAST to be executed FIRST)
app.add_middleware(MerchantApiLoggingMiddleware)
app.add_middleware(LoggingMiddleware)
app.add_middleware(RequestIdMiddleware)

# Error Handlers
setup_error_handlers(app)

# Prometheus /metrics — keep this after middleware setup so metric labels are
# computed against the final route table. Scraped by the observability stack
# only; do not proxy it through the public nginx.
setup_metrics(app)

# Routers
app.include_router(api_router, prefix="/api/v1")
app.include_router(merchant_router, prefix="/api/merchant/v1")
app.include_router(payout_merchant_router, prefix="/api/merchant/payout/v1")
app.include_router(bot_router, prefix="/api/bot/v1")
# Public webhook surface for external cascade providers — no auth on the
# router level; each adapter validates its own signature scheme inside
# parse_callback (failed signatures raise UnauthorizedException → 401).
app.include_router(cascade_callbacks_router, prefix="/api/cascade/v1")

# Admin-gated Swagger on every environment (dev/staging/prod). The built-in
# public Swagger is turned off above; this router exposes
# `/api/docs`, `/api/redoc`, `/api/openapi.json` but each endpoint runs
# through `verify_admin_access`, which requires the `access_token` cookie
# to be set by a successful admin login. Non-admins and anonymous users
# get a 404.
from app.api.docs import router as admin_docs_router
app.include_router(admin_docs_router, prefix="/api")


@app.get("/api/merchant/docs", include_in_schema=False)
async def get_merchant_swagger_ui():
    return get_swagger_ui_html(
        openapi_url="/api/merchant/openapi.json",
        title="Merchant API Docs",
    )


@app.get("/api/merchant/redoc", include_in_schema=False)
async def get_merchant_redoc_html():
    return get_redoc_html(
        openapi_url="/api/merchant/openapi.json",
        title="Merchant API ReDoc",
    )


@app.get("/api/merchant/openapi.json", include_in_schema=False)
async def get_merchant_openapi(request: Request):
    merchant_routes = [
        route for route in request.app.routes 
        if getattr(route, "path", "").startswith("/api/merchant/v1")
    ]
    return get_openapi(
        title="psmini Merchant API",
        version="1.0.0",
        routes=merchant_routes,
    )


@app.get("/health")
async def health_check():
    """Liveness: the process and event loop are running. Intentionally shallow —
    a transient DB/Redis blip must NOT cause the orchestrator to kill the pod."""
    return {"status": "ok"}


@app.get("/health/ready", include_in_schema=False)
async def health_ready():
    """Readiness: actively probe DB / Redis / ClickHouse. Returns 503 when any
    required dependency is down so the load balancer / orchestrator drains this
    node instead of routing traffic into a broken instance, and a rolling deploy
    waits for a real ready signal. Per-dependency status is in the body and is
    also published as the `primepay_dependency_up` Prometheus gauge."""
    ok, deps = await check_readiness()
    return JSONResponse(
        status_code=200 if ok else 503,
        content={"status": "ready" if ok else "degraded", "dependencies": deps},
    )
