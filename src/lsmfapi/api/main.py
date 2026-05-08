import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from lsmfapi.api.routers import accuracy, dashboard, forecast
from lsmfapi.database import telemetry
from lsmfapi.database.cache import cache_stats, load_cache, save_cache
from lsmfapi.database.db import init_db
from lsmfapi.scheduler import CollectorScheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

_scheduler: CollectorScheduler | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _scheduler
    from lsmfapi._eccodes import setup_definitions
    setup_definitions()
    init_db()
    load_cache()
    _scheduler = CollectorScheduler()
    await _scheduler.startup()
    yield
    if _scheduler:
        _scheduler.shutdown()
    save_cache()


app = FastAPI(title="LSMFAPI", version="0.1.0", lifespan=lifespan)


class TelemetryMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/"):
            telemetry.record_request(request.method, path)
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


app.add_middleware(TelemetryMiddleware)
app.include_router(forecast.router)
app.include_router(accuracy.router)
app.include_router(dashboard.router)
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse(url="/dashboard")


@app.get("/health", include_in_schema=False)
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok", **cache_stats()})
