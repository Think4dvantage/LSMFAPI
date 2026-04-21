import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from lsmfapi.models.forecast import (
    AltitudeWindsResponse,
    GridWindCache,
    StationForecastResponse,
)

logger = logging.getLogger(__name__)

CACHE_FILE = Path("/app/data/cache.json")
GRID_CACHE_FILE = Path("/app/data/grid_cache.npz")

_station_cache: dict[str, StationForecastResponse] = {}
_altitude_winds_cache: dict[str, AltitudeWindsResponse] = {}
_grid_wind_cache: GridWindCache | None = None
_last_populated_at: datetime | None = None


def get_station_forecast(station_key: str) -> StationForecastResponse | None:
    return _station_cache.get(station_key)


def set_station_forecast(station_key: str, data: StationForecastResponse) -> None:
    global _last_populated_at
    _station_cache[station_key] = data
    _last_populated_at = datetime.utcnow()


def known_stations() -> frozenset[str]:
    return frozenset(_station_cache.keys())


def cache_is_warm() -> bool:
    return bool(_station_cache)


def get_station_altitude_winds(station_key: str) -> AltitudeWindsResponse | None:
    return _altitude_winds_cache.get(station_key)


def set_station_altitude_winds(station_key: str, data: AltitudeWindsResponse) -> None:
    _altitude_winds_cache[station_key] = data


def get_grid_wind_cache() -> GridWindCache | None:
    return _grid_wind_cache


def set_grid_wind_cache(data: GridWindCache) -> None:
    global _grid_wind_cache
    _grid_wind_cache = data


def save_cache() -> None:
    """Atomically write all caches to disk."""
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "station": {k: v.model_dump(mode="json") for k, v in _station_cache.items()},
            "altitude_winds": {k: v.model_dump(mode="json") for k, v in _altitude_winds_cache.items()},
        }
        tmp = CACHE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(CACHE_FILE)
        logger.info("Cache saved: %d stations → %s", len(_station_cache), CACHE_FILE)
    except Exception:
        logger.exception("Failed to save station/altitude-winds cache")

    _save_grid_cache()


def _save_grid_cache() -> None:
    if _grid_wind_cache is None:
        return
    gc = _grid_wind_cache
    try:
        GRID_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        arrays: dict[str, np.ndarray] = {
            "lats": gc.lats,
            "lons": gc.lons,
            "rh":   gc.rh,
        }
        for level_m, arr in gc.ws.items():
            arrays[f"ws_{level_m}"] = arr
        for level_m, arr in gc.wd.items():
            arrays[f"wd_{level_m}"] = arr

        valid_times_unix = np.array(
            [vt.timestamp() for vt in gc.valid_times], dtype=np.float64
        )
        meta = np.array([
            gc.init_time.timestamp(),
            gc.n_lat, gc.n_lon,
            gc.lat_max, gc.lon_min, gc.step_deg,
        ], dtype=np.float64)

        tmp = GRID_CACHE_FILE.with_suffix(".tmp.npz")
        np.savez_compressed(
            str(tmp),
            _meta=meta,
            _valid_times=valid_times_unix,
            **arrays,
        )
        tmp.replace(GRID_CACHE_FILE)
        logger.info(
            "Grid cache saved: %d × %d, %d levels, %d frames → %s (%.1f MB)",
            gc.n_lat, gc.n_lon, len(gc.ws), len(gc.valid_times),
            GRID_CACHE_FILE,
            GRID_CACHE_FILE.stat().st_size / 1_048_576,
        )
    except Exception:
        logger.exception("Failed to save grid cache")


def load_cache() -> None:
    """Populate all in-memory caches from disk files."""
    global _station_cache, _altitude_winds_cache, _last_populated_at
    if not CACHE_FILE.exists():
        logger.info("No cache file at %s — starting fresh", CACHE_FILE)
    else:
        try:
            data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            _station_cache = {
                k: StationForecastResponse.model_validate(v)
                for k, v in data.get("station", {}).items()
            }
            _altitude_winds_cache = {
                k: AltitudeWindsResponse.model_validate(v)
                for k, v in data.get("altitude_winds", {}).items()
            }
            if _station_cache:
                _last_populated_at = datetime.utcnow()
            logger.info(
                "Cache loaded: %d stations, %d altitude-wind entries from %s",
                len(_station_cache), len(_altitude_winds_cache), CACHE_FILE,
            )
        except Exception:
            logger.exception("Failed to load station/altitude-winds cache — starting fresh")

    _load_grid_cache()


def _load_grid_cache() -> None:
    global _grid_wind_cache
    if not GRID_CACHE_FILE.exists():
        logger.info("No grid cache file at %s", GRID_CACHE_FILE)
        return
    try:
        npz = np.load(str(GRID_CACHE_FILE), allow_pickle=False)

        meta = npz["_meta"]
        init_time = datetime.fromtimestamp(float(meta[0]), tz=timezone.utc)
        n_lat      = int(meta[1])
        n_lon      = int(meta[2])
        lat_max    = float(meta[3])
        lon_min    = float(meta[4])
        step_deg   = float(meta[5])

        valid_times = [
            datetime.fromtimestamp(float(ts), tz=timezone.utc)
            for ts in npz["_valid_times"]
        ]

        ws: dict[int, np.ndarray] = {}
        wd: dict[int, np.ndarray] = {}
        for key in npz.files:
            if key.startswith("ws_"):
                ws[int(key[3:])] = npz[key]
            elif key.startswith("wd_"):
                wd[int(key[3:])] = npz[key]

        _grid_wind_cache = GridWindCache(
            init_time=init_time,
            lats=npz["lats"],
            lons=npz["lons"],
            n_lat=n_lat,
            n_lon=n_lon,
            lat_max=lat_max,
            lon_min=lon_min,
            step_deg=step_deg,
            valid_times=valid_times,
            ws=ws,
            wd=wd,
            rh=npz["rh"],
        )
        logger.info(
            "Grid cache loaded: %d × %d, %d levels, %d frames from %s",
            n_lat, n_lon, len(ws), len(valid_times), GRID_CACHE_FILE,
        )
    except Exception:
        logger.exception("Failed to load grid cache — will regenerate on next collection")


def cache_stats() -> dict:
    return {
        "station_cache_keys": len(_station_cache),
        "grid_wind_cache": _grid_wind_cache is not None,
        "last_populated_at": _last_populated_at.isoformat() if _last_populated_at else None,
    }


def station_cache_detail() -> dict:
    if not _station_cache:
        return {"count": 0, "model": None, "init_time": None, "forecast_hours": 0, "valid_until": None}
    sample = next(iter(_station_cache.values()))
    valid_until = None
    if sample.init_time and sample.forecast:
        from datetime import timedelta
        valid_until = (sample.init_time + timedelta(hours=len(sample.forecast))).isoformat()
    return {
        "count": len(_station_cache),
        "model": sample.model,
        "init_time": sample.init_time.isoformat() if sample.init_time else None,
        "forecast_hours": len(sample.forecast),
        "valid_until": valid_until,
    }


def altitude_winds_cache_detail() -> dict:
    return {"count": len(_altitude_winds_cache)}


def grid_cache_detail() -> dict:
    if _grid_wind_cache is None:
        return {"warm": False}
    gc = _grid_wind_cache
    valid_until = gc.valid_times[-1].isoformat() if gc.valid_times else None
    return {
        "warm": True,
        "init_time": gc.init_time.isoformat(),
        "n_points": gc.n_lat * gc.n_lon,
        "n_lat": gc.n_lat,
        "n_lon": gc.n_lon,
        "forecast_hours": len(gc.valid_times),
        "valid_until": valid_until,
        "levels_m": sorted(gc.ws.keys()),
    }
