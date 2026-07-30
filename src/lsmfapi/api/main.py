import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from importlib.metadata import version as pkg_version

from fastapi import FastAPI
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from lsmfapi.api.routers import dashboard, forecast
from lsmfapi.config import get_config
from lsmfapi.database import telemetry
from lsmfapi.database.cache import cache_stats, load_cache, save_cache
from lsmfapi.database.db import check_db, init_db
from lsmfapi.scheduler import CollectorScheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

_scheduler: CollectorScheduler | None = None
_SERVICE_VERSION = pkg_version("lsmfapi")
_started_monotonic = time.monotonic()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _scheduler
    from lsmfapi._eccodes import setup_definitions
    setup_definitions()
    get_config()  # fail fast + log resolved values before anything else starts
    init_db()
    load_cache()
    _scheduler = CollectorScheduler()
    await _scheduler.startup()
    yield
    if _scheduler:
        _scheduler.shutdown()
    # Synchronous and blocking on purpose: the loop is closing here, and losing the
    # cache on a fast shutdown is worse than a slow one.
    save_cache()


app = FastAPI(title="LSMFAPI", version=_SERVICE_VERSION, lifespan=lifespan)


class TelemetryMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/"):
            telemetry.record_request()
        response = await call_next(request)
        if path.startswith("/api/") and response.status_code >= 400:
            body = b""
            async for chunk in response.body_iterator:
                body += chunk
            detail = body.decode("utf-8", errors="replace")[:400]
            telemetry.record_error(request.method, path, response.status_code, detail)
            headers = {k: v for k, v in response.headers.items() if k.lower() != "content-length"}
            return Response(content=body, status_code=response.status_code,
                            headers=headers, media_type=response.media_type)
        return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    # Never put exception text in the response body — log it, return a generic message.
    # TelemetryMiddleware records this like any other >=400 response once it's a real Response.
    logger.error("Unhandled exception on %s %s", request.method, request.url.path, exc_info=True)
    return JSONResponse(
        {"error": {"code": "internal_error", "message": "Internal server error"}},
        status_code=500,
    )


app.add_middleware(TelemetryMiddleware)
app.include_router(forecast.router)
app.include_router(dashboard.router)
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse(url="/dashboard")


@app.get("/health", include_in_schema=False)
async def health() -> JSONResponse:
    checks: dict[str, str] = {}
    healthy = True

    db_ok = check_db()
    checks["sqlite"] = "ok" if db_ok else "unreachable"
    healthy &= db_ok

    scheduler_running = bool(_scheduler and _scheduler.is_running())
    job_count = _scheduler.job_count() if _scheduler else 0
    checks["scheduler"] = f"running ({job_count} jobs)" if scheduler_running else "stopped"
    healthy &= scheduler_running

    stats = cache_stats()
    warm = bool(stats["ch1_station_cache_keys"] or stats["ch2_station_cache_keys"])
    last_populated_at = stats["last_populated_at"]
    if warm and last_populated_at:
        age_s = (datetime.now(timezone.utc) - datetime.fromisoformat(last_populated_at)).total_seconds()
        checks["cache"] = f"warm (age {age_s:.0f}s)"
    else:
        checks["cache"] = "warm" if warm else "cold"
    # Stale-but-serving is intentional behaviour — never 503 on cache age alone.

    body = {
        "status": "ok" if healthy else "degraded",
        "service": "lsmfapi",
        "version": _SERVICE_VERSION,
        "uptime_seconds": round(time.monotonic() - _started_monotonic, 1),
        "checks": checks,
    }
    if not healthy:
        logger.warning("Health check degraded: %s", checks)
        return JSONResponse(body, status_code=503)
    return JSONResponse(body)
