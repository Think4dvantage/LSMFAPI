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
GRID_CACHE_FILE_CH1 = Path("/app/data/grid_cache_ch1.npz")
GRID_CACHE_FILE_CH2 = Path("/app/data/grid_cache_ch2.npz")

_ch1_station_cache: dict[str, StationForecastResponse] = {}
_ch2_station_cache: dict[str, StationForecastResponse] = {}
_ch1_altitude_winds_cache: dict[str, AltitudeWindsResponse] = {}
_ch2_altitude_winds_cache: dict[str, AltitudeWindsResponse] = {}
_ch1_grid_wind_cache: GridWindCache | None = None
_ch2_grid_wind_cache: GridWindCache | None = None
_last_populated_at: datetime | None = None


def _merge_station_forecasts(
    ch1: StationForecastResponse, ch2: StationForecastResponse
) -> StationForecastResponse:
    """Append CH2 steps that fall strictly after the last CH1 valid_time."""
    cutoff = max(h.valid_time for h in ch1.forecast) if ch1.forecast else None
    tail = [h for h in ch2.forecast if cutoff is None or h.valid_time > cutoff]
    return ch1.model_copy(update={"forecast": ch1.forecast + tail})


def _merge_altitude_winds(
    ch1: AltitudeWindsResponse, ch2: AltitudeWindsResponse
) -> AltitudeWindsResponse:
    """Append CH2 profiles that fall strictly after the last CH1 valid_time."""
    cutoff = max(p.valid_time for p in ch1.profiles) if ch1.profiles else None
    tail = [p for p in ch2.profiles if cutoff is None or p.valid_time > cutoff]
    return ch1.model_copy(update={"profiles": ch1.profiles + tail})


def get_station_forecast(station_key: str) -> StationForecastResponse | None:
    ch1 = _ch1_station_cache.get(station_key)
    ch2 = _ch2_station_cache.get(station_key)
    if ch1 is not None and ch2 is not None:
        return _merge_station_forecasts(ch1, ch2)
    return ch1 or ch2


def set_station_forecast(station_key: str, data: StationForecastResponse) -> None:
    global _last_populated_at
    if data.model == "icon-ch1":
        _ch1_station_cache[station_key] = data
    else:
        _ch2_station_cache[station_key] = data
    _last_populated_at = datetime.utcnow()


def known_stations() -> frozenset[str]:
    return frozenset(_ch1_station_cache.keys()) | frozenset(_ch2_station_cache.keys())


def cache_is_warm() -> bool:
    return bool(_ch1_station_cache or _ch2_station_cache)


def get_station_altitude_winds(station_key: str) -> AltitudeWindsResponse | None:
    ch1 = _ch1_altitude_winds_cache.get(station_key)
    ch2 = _ch2_altitude_winds_cache.get(station_key)
    if ch1 is not None and ch2 is not None:
        return _merge_altitude_winds(ch1, ch2)
    return ch1 or ch2


def set_station_altitude_winds(station_key: str, data: AltitudeWindsResponse) -> None:
    if data.model == "icon-ch1":
        _ch1_altitude_winds_cache[station_key] = data
    else:
        _ch2_altitude_winds_cache[station_key] = data


def _merge_grid_caches(ch1: GridWindCache, ch2: GridWindCache) -> GridWindCache:
    """Append CH2 frames strictly after the last CH1 valid_time."""
    cutoff = ch1.valid_times[-1] if ch1.valid_times else None
    ch2_idx = [i for i, vt in enumerate(ch2.valid_times) if cutoff is None or vt > cutoff]
    if not ch2_idx:
        return ch1
    idx = np.array(ch2_idx)
    merged_ws = {m: np.concatenate([ch1.ws[m], ch2.ws[m][idx]], axis=0) for m in ch1.ws if m in ch2.ws}
    merged_wd = {m: np.concatenate([ch1.wd[m], ch2.wd[m][idx]], axis=0) for m in ch1.wd if m in ch2.wd}
    return GridWindCache(
        model="icon-ch1+ch2",
        init_time=ch1.init_time,
        lats=ch1.lats, lons=ch1.lons,
        n_lat=ch1.n_lat, n_lon=ch1.n_lon,
        lat_max=ch1.lat_max, lon_min=ch1.lon_min,
        step_deg=ch1.step_deg,
        valid_times=ch1.valid_times + [ch2.valid_times[i] for i in ch2_idx],
        ws=merged_ws,
        wd=merged_wd,
        rh=np.concatenate([ch1.rh, ch2.rh[idx]], axis=0),
    )


def get_grid_wind_cache() -> GridWindCache | None:
    ch1, ch2 = _ch1_grid_wind_cache, _ch2_grid_wind_cache
    if ch1 is not None and ch2 is not None:
        return _merge_grid_caches(ch1, ch2)
    return ch1 or ch2


def set_grid_wind_cache(data: GridWindCache) -> None:
    global _ch1_grid_wind_cache, _ch2_grid_wind_cache
    if data.model == "icon-ch1":
        _ch1_grid_wind_cache = data
    else:
        _ch2_grid_wind_cache = data


def save_cache() -> None:
    """Atomically write all caches to disk."""
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "ch1_station": {k: v.model_dump(mode="json") for k, v in _ch1_station_cache.items()},
            "ch2_station": {k: v.model_dump(mode="json") for k, v in _ch2_station_cache.items()},
            "ch1_altitude_winds": {k: v.model_dump(mode="json") for k, v in _ch1_altitude_winds_cache.items()},
            "ch2_altitude_winds": {k: v.model_dump(mode="json") for k, v in _ch2_altitude_winds_cache.items()},
        }
        tmp = CACHE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(CACHE_FILE)
        logger.info(
            "Cache saved: %d CH1 + %d CH2 stations → %s",
            len(_ch1_station_cache), len(_ch2_station_cache), CACHE_FILE,
        )
    except Exception:
        logger.exception("Failed to save station/altitude-winds cache")

    _save_grid_cache()


def _save_grid_cache() -> None:
    for gc, path in (
        (_ch1_grid_wind_cache, GRID_CACHE_FILE_CH1),
        (_ch2_grid_wind_cache, GRID_CACHE_FILE_CH2),
    ):
        if gc is None:
            continue
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            arrays: dict[str, np.ndarray] = {"lats": gc.lats, "lons": gc.lons, "rh": gc.rh}
            for level_m, arr in gc.ws.items():
                arrays[f"ws_{level_m}"] = arr
            for level_m, arr in gc.wd.items():
                arrays[f"wd_{level_m}"] = arr
            meta = np.array([
                gc.init_time.timestamp(), gc.n_lat, gc.n_lon,
                gc.lat_max, gc.lon_min, gc.step_deg,
            ], dtype=np.float64)
            tmp = path.with_suffix(".tmp.npz")
            np.savez_compressed(
                str(tmp),
                _meta=meta,
                _valid_times=np.array([vt.timestamp() for vt in gc.valid_times], dtype=np.float64),
                **arrays,
            )
            tmp.replace(path)
            logger.info(
                "Grid cache saved (%s): %d × %d, %d levels, %d frames → %s (%.1f MB)",
                gc.model, gc.n_lat, gc.n_lon, len(gc.ws), len(gc.valid_times),
                path, path.stat().st_size / 1_048_576,
            )
        except Exception:
            logger.exception("Failed to save grid cache (%s)", gc.model)


def load_cache() -> None:
    """Populate all in-memory caches from disk files."""
    global _ch1_station_cache, _ch2_station_cache
    global _ch1_altitude_winds_cache, _ch2_altitude_winds_cache
    global _last_populated_at

    if not CACHE_FILE.exists():
        logger.info("No cache file at %s — starting fresh", CACHE_FILE)
    else:
        try:
            data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            _ch1_station_cache = {
                k: StationForecastResponse.model_validate(v)
                for k, v in data.get("ch1_station", {}).items()
            }
            _ch2_station_cache = {
                k: StationForecastResponse.model_validate(v)
                for k, v in data.get("ch2_station", {}).items()
            }
            _ch1_altitude_winds_cache = {
                k: AltitudeWindsResponse.model_validate(v)
                for k, v in data.get("ch1_altitude_winds", {}).items()
            }
            _ch2_altitude_winds_cache = {
                k: AltitudeWindsResponse.model_validate(v)
                for k, v in data.get("ch2_altitude_winds", {}).items()
            }
            if _ch1_station_cache or _ch2_station_cache:
                _last_populated_at = datetime.utcnow()
            logger.info(
                "Cache loaded: %d CH1 + %d CH2 stations from %s",
                len(_ch1_station_cache), len(_ch2_station_cache), CACHE_FILE,
            )
        except Exception:
            logger.exception("Failed to load station/altitude-winds cache — starting fresh")

    _load_grid_cache()


def _load_grid_cache() -> None:
    global _ch1_grid_wind_cache, _ch2_grid_wind_cache
    for path, model in ((GRID_CACHE_FILE_CH1, "icon-ch1"), (GRID_CACHE_FILE_CH2, "icon-ch2")):
        if not path.exists():
            continue
        try:
            npz = np.load(str(path), allow_pickle=False)
            meta = npz["_meta"]
            init_time = datetime.fromtimestamp(float(meta[0]), tz=timezone.utc)
            n_lat, n_lon = int(meta[1]), int(meta[2])
            lat_max, lon_min, step_deg = float(meta[3]), float(meta[4]), float(meta[5])
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
            gc = GridWindCache(
                model=model,
                init_time=init_time,
                lats=npz["lats"], lons=npz["lons"],
                n_lat=n_lat, n_lon=n_lon,
                lat_max=lat_max, lon_min=lon_min, step_deg=step_deg,
                valid_times=valid_times, ws=ws, wd=wd, rh=npz["rh"],
            )
            if model == "icon-ch1":
                _ch1_grid_wind_cache = gc
            else:
                _ch2_grid_wind_cache = gc
            logger.info(
                "Grid cache loaded (%s): %d × %d, %d levels, %d frames from %s",
                model, n_lat, n_lon, len(ws), len(valid_times), path,
            )
        except Exception:
            logger.exception("Failed to load grid cache (%s) — will regenerate on next collection", model)


def cache_stats() -> dict:
    return {
        "ch1_station_cache_keys": len(_ch1_station_cache),
        "ch2_station_cache_keys": len(_ch2_station_cache),
        "grid_wind_cache": _ch1_grid_wind_cache is not None or _ch2_grid_wind_cache is not None,
        "last_populated_at": _last_populated_at.isoformat() if _last_populated_at else None,
    }


def station_cache_detail() -> dict:
    if not _ch1_station_cache and not _ch2_station_cache:
        return {
            "count": 0, "ch1": None, "ch2": None,
            "combined_forecast_hours": 0, "init_time": None, "valid_until": None,
        }

    def _model_detail(cache: dict[str, StationForecastResponse]) -> dict | None:
        if not cache:
            return None
        sample = next(iter(cache.values()))
        return {
            "count": len(cache),
            "model": sample.model,
            "init_time": sample.init_time.isoformat() if sample.init_time else None,
            "forecast_hours": len(sample.forecast),
        }

    all_keys = frozenset(_ch1_station_cache.keys()) | frozenset(_ch2_station_cache.keys())
    sample_key = next(iter(all_keys))
    merged = get_station_forecast(sample_key)
    combined_hours = len(merged.forecast) if merged else 0
    valid_until = merged.forecast[-1].valid_time.isoformat() if (merged and merged.forecast) else None

    ch1_detail = _model_detail(_ch1_station_cache)
    return {
        "count": len(all_keys),
        "ch1": ch1_detail,
        "ch2": _model_detail(_ch2_station_cache),
        "combined_forecast_hours": combined_hours,
        "init_time": ch1_detail["init_time"] if ch1_detail else None,
        "valid_until": valid_until,
    }


def altitude_winds_cache_detail() -> dict:
    all_keys = frozenset(_ch1_altitude_winds_cache.keys()) | frozenset(_ch2_altitude_winds_cache.keys())
    return {
        "count": len(all_keys),
        "ch1_count": len(_ch1_altitude_winds_cache),
        "ch2_count": len(_ch2_altitude_winds_cache),
    }


def grid_cache_detail() -> dict:
    merged = get_grid_wind_cache()
    if merged is None:
        return {"warm": False}
    valid_until = merged.valid_times[-1].isoformat() if merged.valid_times else None
    return {
        "warm": True,
        "init_time": merged.init_time.isoformat(),
        "n_points": merged.n_lat * merged.n_lon,
        "n_lat": merged.n_lat,
        "n_lon": merged.n_lon,
        "forecast_hours": len(merged.valid_times),
        "valid_until": valid_until,
        "levels_m": sorted(merged.ws.keys()),
        "ch1_frames": len(_ch1_grid_wind_cache.valid_times) if _ch1_grid_wind_cache else 0,
        "ch2_frames": len(_ch2_grid_wind_cache.valid_times) if _ch2_grid_wind_cache else 0,
    }
