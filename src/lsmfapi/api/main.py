import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from lsmfapi.api.routers import accuracy, forecast
from lsmfapi.database.cache import cache_stats
from lsmfapi.database.db import init_db
from lsmfapi.scheduler import CollectorScheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

_scheduler: CollectorScheduler | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _scheduler
    from lsmfapi._eccodes import setup_definitions
    setup_definitions()
    init_db()
    _scheduler = CollectorScheduler()
    await _scheduler.startup()
    yield
    if _scheduler:
        _scheduler.shutdown()


app = FastAPI(title="LSMFAPI", version="0.1.0", lifespan=lifespan)
app.include_router(forecast.router)
app.include_router(accuracy.router)
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/health", include_in_schema=False)
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok", **cache_stats()})
