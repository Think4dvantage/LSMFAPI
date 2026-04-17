import json
import logging
from datetime import datetime
from pathlib import Path

from lsmfapi.models.forecast import AltitudeWindsResponse, ForecastResponse, GridResponse

logger = logging.getLogger(__name__)

CACHE_FILE = Path("/app/data/cache.json")

_station_cache: dict[str, ForecastResponse] = {}
_altitude_winds_cache: dict[str, AltitudeWindsResponse] = {}
_grid_cache: dict[str, GridResponse] = {}
_last_populated_at: datetime | None = None


def get_station_forecast(station_key: str) -> ForecastResponse | None:
    return _station_cache.get(station_key)


def set_station_forecast(station_key: str, data: ForecastResponse) -> None:
    global _last_populated_at
    _station_cache[station_key] = data
    _last_populated_at = datetime.utcnow()


def get_station_altitude_winds(station_key: str) -> AltitudeWindsResponse | None:
    return _altitude_winds_cache.get(station_key)


def set_station_altitude_winds(station_key: str, data: AltitudeWindsResponse) -> None:
    global _last_populated_at
    _altitude_winds_cache[station_key] = data
    _last_populated_at = datetime.utcnow()


def get_grid_forecast(date: str, level_m: int) -> GridResponse | None:
    return _grid_cache.get(f"{date}_{level_m}")


def set_grid_forecast(date: str, level_m: int, data: GridResponse) -> None:
    global _last_populated_at
    _grid_cache[f"{date}_{level_m}"] = data
    _last_populated_at = datetime.utcnow()


def save_cache() -> None:
    """Atomically write all in-memory caches to CACHE_FILE."""
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "station": {k: v.model_dump(mode="json") for k, v in _station_cache.items()},
            "altitude_winds": {k: v.model_dump(mode="json") for k, v in _altitude_winds_cache.items()},
            "grid": {k: v.model_dump(mode="json") for k, v in _grid_cache.items()},
        }
        tmp = CACHE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(CACHE_FILE)
        logger.info("Cache saved: %d stations → %s", len(_station_cache), CACHE_FILE)
    except Exception:
        logger.exception("Failed to save cache")


def load_cache() -> None:
    """Populate in-memory caches from CACHE_FILE if it exists."""
    global _station_cache, _altitude_winds_cache, _grid_cache, _last_populated_at
    if not CACHE_FILE.exists():
        logger.info("No cache file at %s — starting fresh", CACHE_FILE)
        return
    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        _station_cache = {
            k: ForecastResponse.model_validate(v)
            for k, v in data.get("station", {}).items()
        }
        _altitude_winds_cache = {
            k: AltitudeWindsResponse.model_validate(v)
            for k, v in data.get("altitude_winds", {}).items()
        }
        _grid_cache = {
            k: GridResponse.model_validate(v)
            for k, v in data.get("grid", {}).items()
        }
        if _station_cache:
            _last_populated_at = datetime.utcnow()
        logger.info(
            "Cache loaded: %d stations, %d altitude-wind entries from %s",
            len(_station_cache), len(_altitude_winds_cache), CACHE_FILE,
        )
    except Exception:
        logger.exception("Failed to load cache — starting fresh")


def cache_stats() -> dict:
    return {
        "station_cache_keys": len(_station_cache),
        "grid_cache_keys": len(_grid_cache),
        "last_populated_at": _last_populated_at.isoformat() if _last_populated_at else None,
    }
