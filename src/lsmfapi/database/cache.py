from datetime import datetime

from lsmfapi.models.forecast import ForecastResponse, GridResponse

_station_cache: dict[str, ForecastResponse] = {}
_grid_cache: dict[str, GridResponse] = {}
_last_populated_at: datetime | None = None


def get_station_forecast(station_key: str) -> ForecastResponse | None:
    return _station_cache.get(station_key)


def set_station_forecast(station_key: str, data: ForecastResponse) -> None:
    global _last_populated_at
    _station_cache[station_key] = data
    _last_populated_at = datetime.utcnow()


def get_grid_forecast(date: str, level_m: int) -> GridResponse | None:
    return _grid_cache.get(f"{date}_{level_m}")


def set_grid_forecast(date: str, level_m: int, data: GridResponse) -> None:
    global _last_populated_at
    _grid_cache[f"{date}_{level_m}"] = data
    _last_populated_at = datetime.utcnow()


def cache_stats() -> dict:
    return {
        "station_cache_keys": len(_station_cache),
        "grid_cache_keys": len(_grid_cache),
        "last_populated_at": _last_populated_at.isoformat() if _last_populated_at else None,
    }
