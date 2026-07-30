import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from lsmfapi.models.forecast import (
    AltitudeWindsResponse,
    GridWindCache,
    StationForecastResponse,
    ThermalGridCache,
)

logger = logging.getLogger(__name__)

CACHE_FILE = Path("/app/data/cache.json")
GRID_CACHE_FILE = Path("/app/data/grid_cache.npz")
THERMAL_GRID_CACHE_FILE = Path("/app/data/thermal_grid_cache.npz")

# Legacy per-model grid files (superseded by the single combined files above).
# Removed on load so they don't linger on the data volume.
_LEGACY_GRID_FILES = (
    Path("/app/data/grid_cache_ch1.npz"),
    Path("/app/data/grid_cache_ch2.npz"),
    Path("/app/data/thermal_grid_cache_ch1.npz"),
    Path("/app/data/thermal_grid_cache_ch2.npz"),
)

# The full forecast axis is horizons 0..120 (121 frames). CH1 fills rows 0..33,
# CH2 fills rows 34..120 — each collector writes its own disjoint, contiguous slice
# into one shared array, so reads need no merge/concatenate copy.
_FULL_N_FRAMES = 121
_CH2_START_HORIZON = 34  # CH2 covers h34+; CH1 covers h0-33 (mirrors collectors HORIZONS)

_THERMAL_BASE = ("solar", "sunshine", "cloud_cover", "cloud_low", "cloud_mid", "cloud_high",
                 "freezing_level", "cape", "cin", "lcl", "lfc", "tke")
_THERMAL_FIELDS = _THERMAL_BASE + tuple(f"{f}_min" for f in _THERMAL_BASE) + tuple(f"{f}_max" for f in _THERMAL_BASE)

_ch1_station_cache: dict[str, StationForecastResponse] = {}
_ch2_station_cache: dict[str, StationForecastResponse] = {}
_ch1_altitude_winds_cache: dict[str, AltitudeWindsResponse] = {}
_ch2_altitude_winds_cache: dict[str, AltitudeWindsResponse] = {}

# One combined store per grid, sized to the full 121-frame axis. Each collector writes
# its horizon slice in place (set_*); reads return a contiguous view (get_*, no copy).
_grid_wind_cache: GridWindCache | None = None
_thermal_grid_cache: ThermalGridCache | None = None
# model id -> init_time of that model's last write, used to report a combined init_time.
_grid_wind_model_init: dict[str, datetime] = {}
_thermal_grid_model_init: dict[str, datetime] = {}
_last_populated_at: datetime | None = None
# Set whenever a collector writes a slice; save_cache() clears it after a
# successful write so an unrelated run's save doesn't rewrite an unchanged grid.
_grid_wind_dirty: bool = False
_thermal_grid_dirty: bool = False


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
    _last_populated_at = datetime.now(timezone.utc)


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


# ---------- Combined grid store helpers ----------

def _horizon_index(valid_time: datetime, init_time: datetime) -> int:
    return int(round((valid_time - init_time).total_seconds() / 3600.0))


def _populated_range(valid_times: list) -> tuple[int, int] | None:
    """Return [start, end) of populated (non-None) frames.

    CH1 (rows 0-33) and CH2 (rows 34-120) always form a single contiguous block,
    so the returned range can be sliced as a view with no gaps.
    """
    idxs = [i for i, vt in enumerate(valid_times) if vt is not None]
    if not idxs:
        return None
    return idxs[0], idxs[-1] + 1


def _combined_model(model_init: dict[str, datetime]) -> str:
    present = [m for m in ("icon-ch1", "icon-ch2") if m in model_init]
    if len(present) == 2:
        return "icon-ch1+ch2"
    return present[0] if present else "combined"


def _reported_init(model_init: dict[str, datetime], fallback: datetime) -> datetime:
    return model_init.get("icon-ch1") or model_init.get("icon-ch2") or fallback


def set_grid_wind_cache(data: GridWindCache) -> None:
    """Write one model's horizon slice into the combined wind-grid store (in place)."""
    global _grid_wind_cache, _grid_wind_dirty
    combined = _grid_wind_cache
    if combined is None:
        n = data.rh.shape[1]
        levels = sorted(data.ws.keys())
        combined = GridWindCache(
            model="combined",
            init_time=data.init_time,
            lats=data.lats, lons=data.lons,
            n_lat=data.n_lat, n_lon=data.n_lon,
            lat_max=data.lat_max, lon_min=data.lon_min, step_deg=data.step_deg,
            valid_times=[None] * _FULL_N_FRAMES,
            ws={lvl: np.full((_FULL_N_FRAMES, n), np.nan, dtype=np.float16) for lvl in levels},
            wd={lvl: np.full((_FULL_N_FRAMES, n), np.nan, dtype=np.float16) for lvl in levels},
            rh=np.full((_FULL_N_FRAMES, n), np.nan, dtype=np.float16),
        )
        _grid_wind_cache = combined

    written = 0
    for i, vt in enumerate(data.valid_times):
        h = _horizon_index(vt, data.init_time)
        if not (0 <= h < _FULL_N_FRAMES):
            continue
        for lvl, arr in data.ws.items():
            if lvl in combined.ws:
                combined.ws[lvl][h] = arr[i]
        for lvl, arr in data.wd.items():
            if lvl in combined.wd:
                combined.wd[lvl][h] = arr[i]
        combined.rh[h] = data.rh[i]
        combined.valid_times[h] = vt
        written += 1
    _grid_wind_model_init[data.model] = data.init_time
    _grid_wind_dirty = True
    logger.info("Wind-grid combined store: wrote %d frames for %s", written, data.model)


def get_grid_wind_cache() -> GridWindCache | None:
    combined = _grid_wind_cache
    if combined is None:
        return None
    rng = _populated_range(combined.valid_times)
    if rng is None:
        return None
    start, end = rng
    return GridWindCache(
        model=_combined_model(_grid_wind_model_init),
        init_time=_reported_init(_grid_wind_model_init, combined.init_time),
        lats=combined.lats, lons=combined.lons,
        n_lat=combined.n_lat, n_lon=combined.n_lon,
        lat_max=combined.lat_max, lon_min=combined.lon_min, step_deg=combined.step_deg,
        valid_times=combined.valid_times[start:end],
        ws={lvl: arr[start:end] for lvl, arr in combined.ws.items()},
        wd={lvl: arr[start:end] for lvl, arr in combined.wd.items()},
        rh=combined.rh[start:end],
    )


def set_thermal_grid_cache(data: ThermalGridCache) -> None:
    """Write one model's horizon slice into the combined thermal-grid store (in place)."""
    global _thermal_grid_cache, _thermal_grid_dirty
    combined = _thermal_grid_cache
    if combined is None:
        n = data.solar.shape[1]
        combined = ThermalGridCache(
            model="combined",
            init_time=data.init_time,
            lats=data.lats, lons=data.lons,
            n_lat=data.n_lat, n_lon=data.n_lon,
            lat_max=data.lat_max, lon_min=data.lon_min, step_deg=data.step_deg,
            valid_times=[None] * _FULL_N_FRAMES,
            **{f: np.full((_FULL_N_FRAMES, n), np.nan, dtype=np.float16) for f in _THERMAL_FIELDS},
        )
        _thermal_grid_cache = combined

    written = 0
    for i, vt in enumerate(data.valid_times):
        h = _horizon_index(vt, data.init_time)
        if not (0 <= h < _FULL_N_FRAMES):
            continue
        for f in _THERMAL_FIELDS:
            getattr(combined, f)[h] = getattr(data, f)[i]
        combined.valid_times[h] = vt
        written += 1
    _thermal_grid_model_init[data.model] = data.init_time
    _thermal_grid_dirty = True
    logger.info("Thermal-grid combined store: wrote %d frames for %s", written, data.model)


def get_thermal_grid_cache() -> ThermalGridCache | None:
    combined = _thermal_grid_cache
    if combined is None:
        return None
    rng = _populated_range(combined.valid_times)
    if rng is None:
        return None
    start, end = rng
    return ThermalGridCache(
        model=_combined_model(_thermal_grid_model_init),
        init_time=_reported_init(_thermal_grid_model_init, combined.init_time),
        lats=combined.lats, lons=combined.lons,
        n_lat=combined.n_lat, n_lon=combined.n_lon,
        lat_max=combined.lat_max, lon_min=combined.lon_min, step_deg=combined.step_deg,
        valid_times=combined.valid_times[start:end],
        **{f: getattr(combined, f)[start:end] for f in _THERMAL_FIELDS},
    )


# ---------- Persistence ----------

def _init_ts(model: str, model_init: dict[str, datetime]) -> float:
    dt = model_init.get(model)
    return dt.timestamp() if dt is not None else float("nan")


def _valid_times_to_array(valid_times: list) -> np.ndarray:
    return np.array(
        [vt.timestamp() if vt is not None else np.nan for vt in valid_times],
        dtype=np.float64,
    )


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

    global _grid_wind_dirty, _thermal_grid_dirty
    if _grid_wind_dirty:
        _save_grid_cache()
        _grid_wind_dirty = False
    if _thermal_grid_dirty:
        _save_thermal_grid_cache()
        _thermal_grid_dirty = False


def _save_grid_cache() -> None:
    combined = _grid_wind_cache
    if combined is None:
        return
    try:
        GRID_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        arrays: dict[str, np.ndarray] = {"lats": combined.lats, "lons": combined.lons, "rh": combined.rh}
        for level_m, arr in combined.ws.items():
            arrays[f"ws_{level_m}"] = arr
        for level_m, arr in combined.wd.items():
            arrays[f"wd_{level_m}"] = arr
        meta = np.array([
            combined.n_lat, combined.n_lon, combined.lat_max, combined.lon_min, combined.step_deg,
            _init_ts("icon-ch1", _grid_wind_model_init), _init_ts("icon-ch2", _grid_wind_model_init),
        ], dtype=np.float64)
        tmp = GRID_CACHE_FILE.with_suffix(".tmp.npz")
        np.savez(
            str(tmp),
            _meta=meta,
            _valid_times=_valid_times_to_array(combined.valid_times),
            **arrays,
        )
        tmp.replace(GRID_CACHE_FILE)
        rng = _populated_range(combined.valid_times)
        n_frames = rng[1] - rng[0] if rng else 0
        logger.info(
            "Grid cache saved: %d × %d, %d levels, %d frames → %s (%.1f MB)",
            combined.n_lat, combined.n_lon, len(combined.ws), n_frames,
            GRID_CACHE_FILE, GRID_CACHE_FILE.stat().st_size / 1_048_576,
        )
    except Exception:
        logger.exception("Failed to save grid cache")


def _save_thermal_grid_cache() -> None:
    combined = _thermal_grid_cache
    if combined is None:
        return
    try:
        THERMAL_GRID_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        meta = np.array([
            combined.n_lat, combined.n_lon, combined.lat_max, combined.lon_min, combined.step_deg,
            _init_ts("icon-ch1", _thermal_grid_model_init), _init_ts("icon-ch2", _thermal_grid_model_init),
        ], dtype=np.float64)
        arrays: dict[str, np.ndarray] = {"lats": combined.lats, "lons": combined.lons}
        for f in _THERMAL_FIELDS:
            arrays[f] = getattr(combined, f)
        tmp = THERMAL_GRID_CACHE_FILE.with_suffix(".tmp.npz")
        np.savez(
            str(tmp),
            _meta=meta,
            _valid_times=_valid_times_to_array(combined.valid_times),
            **arrays,
        )
        tmp.replace(THERMAL_GRID_CACHE_FILE)
        rng = _populated_range(combined.valid_times)
        n_frames = rng[1] - rng[0] if rng else 0
        logger.info(
            "Thermal grid cache saved: %d × %d, %d frames → %s (%.1f MB)",
            combined.n_lat, combined.n_lon, n_frames,
            THERMAL_GRID_CACHE_FILE, THERMAL_GRID_CACHE_FILE.stat().st_size / 1_048_576,
        )
    except Exception:
        logger.exception("Failed to save thermal grid cache")


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
                _last_populated_at = datetime.now(timezone.utc)
            logger.info(
                "Cache loaded: %d CH1 + %d CH2 stations from %s",
                len(_ch1_station_cache), len(_ch2_station_cache), CACHE_FILE,
            )
        except Exception:
            logger.exception("Failed to load station/altitude-winds cache — starting fresh")

    _load_grid_cache()
    _load_thermal_grid_cache()
    _remove_legacy_grid_files()
    _remove_orphaned_tmp_files()


def _load_valid_times(vt_arr: np.ndarray) -> list:
    return [
        None if np.isnan(ts) else datetime.fromtimestamp(float(ts), tz=timezone.utc)
        for ts in vt_arr
    ]


def _restore_model_init(target: dict[str, datetime], ch1_ts: float, ch2_ts: float) -> None:
    if not np.isnan(ch1_ts):
        target["icon-ch1"] = datetime.fromtimestamp(float(ch1_ts), tz=timezone.utc)
    if not np.isnan(ch2_ts):
        target["icon-ch2"] = datetime.fromtimestamp(float(ch2_ts), tz=timezone.utc)


def _load_grid_cache() -> None:
    global _grid_wind_cache
    if not GRID_CACHE_FILE.exists():
        return
    try:
        npz = np.load(str(GRID_CACHE_FILE), allow_pickle=False)
        meta = npz["_meta"]
        n_lat, n_lon = int(meta[0]), int(meta[1])
        lat_max, lon_min, step_deg = float(meta[2]), float(meta[3]), float(meta[4])
        _restore_model_init(_grid_wind_model_init, meta[5], meta[6])
        valid_times = _load_valid_times(npz["_valid_times"])
        ws: dict[int, np.ndarray] = {}
        wd: dict[int, np.ndarray] = {}
        for key in npz.files:
            if key.startswith("ws_"):
                ws[int(key[3:])] = npz[key]
            elif key.startswith("wd_"):
                wd[int(key[3:])] = npz[key]
        _grid_wind_cache = GridWindCache(
            model="combined",
            init_time=_reported_init(_grid_wind_model_init, datetime.now(timezone.utc)),
            lats=npz["lats"], lons=npz["lons"],
            n_lat=n_lat, n_lon=n_lon,
            lat_max=lat_max, lon_min=lon_min, step_deg=step_deg,
            valid_times=valid_times, ws=ws, wd=wd, rh=npz["rh"],
        )
        rng = _populated_range(valid_times)
        logger.info(
            "Grid cache loaded: %d × %d, %d levels, %d frames from %s",
            n_lat, n_lon, len(ws), (rng[1] - rng[0]) if rng else 0, GRID_CACHE_FILE,
        )
    except Exception:
        logger.exception("Failed to load grid cache — will regenerate on next collection")


def _load_thermal_grid_cache() -> None:
    global _thermal_grid_cache
    if not THERMAL_GRID_CACHE_FILE.exists():
        return
    try:
        npz = np.load(str(THERMAL_GRID_CACHE_FILE), allow_pickle=False)
        meta = npz["_meta"]
        n_lat, n_lon = int(meta[0]), int(meta[1])
        lat_max, lon_min, step_deg = float(meta[2]), float(meta[3]), float(meta[4])
        _restore_model_init(_thermal_grid_model_init, meta[5], meta[6])
        valid_times = _load_valid_times(npz["_valid_times"])
        _thermal_grid_cache = ThermalGridCache(
            model="combined",
            init_time=_reported_init(_thermal_grid_model_init, datetime.now(timezone.utc)),
            lats=npz["lats"], lons=npz["lons"],
            n_lat=n_lat, n_lon=n_lon,
            lat_max=lat_max, lon_min=lon_min, step_deg=step_deg,
            valid_times=valid_times,
            **{f: npz[f] for f in _THERMAL_FIELDS},
        )
        rng = _populated_range(valid_times)
        logger.info(
            "Thermal grid cache loaded: %d × %d, %d frames from %s",
            n_lat, n_lon, (rng[1] - rng[0]) if rng else 0, THERMAL_GRID_CACHE_FILE,
        )
    except Exception:
        logger.exception("Failed to load thermal grid cache — will regenerate on next collection")


def _remove_legacy_grid_files() -> None:
    for path in _LEGACY_GRID_FILES:
        try:
            if path.exists():
                path.unlink()
                logger.info("Removed legacy grid cache file %s", path)
        except Exception:
            logger.warning("Could not remove legacy grid cache file %s", path, exc_info=True)


def _remove_orphaned_tmp_files() -> None:
    """A *.tmp/*.tmp.npz present at startup is by definition an interrupted write —
    the atomic tmp->rename pattern never leaves one behind on a clean save."""
    data_dir = CACHE_FILE.parent
    if not data_dir.exists():
        return
    for pattern in ("*.tmp.npz", "*.tmp"):
        for path in data_dir.glob(pattern):
            try:
                path.unlink()
                logger.info("Removed orphaned temp cache file %s", path)
            except Exception:
                logger.warning("Could not remove orphaned temp cache file %s", path, exc_info=True)


def cache_stats() -> dict:
    return {
        "ch1_station_cache_keys": len(_ch1_station_cache),
        "ch2_station_cache_keys": len(_ch2_station_cache),
        "grid_wind_cache": _grid_wind_cache is not None,
        "thermal_grid_cache": _thermal_grid_cache is not None,
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


def _model_frame_counts(combined_valid_times: list) -> tuple[int, int]:
    ch1 = sum(1 for vt in combined_valid_times[:_CH2_START_HORIZON] if vt is not None)
    ch2 = sum(1 for vt in combined_valid_times[_CH2_START_HORIZON:] if vt is not None)
    return ch1, ch2


def grid_cache_detail() -> dict:
    merged = get_grid_wind_cache()
    if merged is None:
        return {"warm": False}
    valid_until = merged.valid_times[-1].isoformat() if merged.valid_times else None
    ch1_frames, ch2_frames = _model_frame_counts(_grid_wind_cache.valid_times)
    return {
        "warm": True,
        "init_time": merged.init_time.isoformat(),
        "n_points": merged.n_lat * merged.n_lon,
        "n_lat": merged.n_lat,
        "n_lon": merged.n_lon,
        "forecast_hours": len(merged.valid_times),
        "valid_until": valid_until,
        "levels_m": sorted(merged.ws.keys()),
        "ch1_frames": ch1_frames,
        "ch2_frames": ch2_frames,
    }


def thermal_grid_cache_detail() -> dict:
    merged = get_thermal_grid_cache()
    if merged is None:
        return {"warm": False}
    valid_until = merged.valid_times[-1].isoformat() if merged.valid_times else None
    ch1_frames, ch2_frames = _model_frame_counts(_thermal_grid_cache.valid_times)
    return {
        "warm": True,
        "init_time": merged.init_time.isoformat(),
        "n_points": merged.n_lat * merged.n_lon,
        "n_lat": merged.n_lat,
        "n_lon": merged.n_lon,
        "forecast_hours": len(merged.valid_times),
        "valid_until": valid_until,
        "ch1_frames": ch1_frames,
        "ch2_frames": ch2_frames,
    }
