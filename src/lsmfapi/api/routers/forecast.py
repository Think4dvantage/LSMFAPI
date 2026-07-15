import logging
import math

import numpy as np
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from lsmfapi.collectors.icon_ch1_eps import (
    ALTITUDE_TO_HPA,
    GRID_LAT_MAX,
    GRID_LAT_MIN,
    GRID_LON_MAX,
    GRID_LON_MIN,
)
from lsmfapi.database.cache import (
    cache_is_warm,
    get_grid_wind_cache,
    get_station_altitude_winds,
    get_station_forecast,
    get_thermal_grid_cache,
    known_stations,
)
from lsmfapi.models.forecast import (
    AltitudeWindsResponse,
    StationForecastResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/forecast", tags=["forecast"])

# Accepted parameter sets for the grid endpoint
_VALID_GRID_LEVELS: frozenset[int] = frozenset(ALTITUDE_TO_HPA.keys()) - {800}
_VALID_STRIDE_KM: frozenset[int] = frozenset({1, 2, 5, 10})

# ICON-CH1 domain envelope used for bbox validation
_DOMAIN_LAT_MIN, _DOMAIN_LAT_MAX = 43.0, 50.0
_DOMAIN_LON_MIN, _DOMAIN_LON_MAX = 3.0, 17.0

# Cap on total emitted values (points × frames × fields) per grid request. A fine
# stride over the full domain would otherwise materialise hundreds of millions of
# Python floats and OOM the container. The default stride_km=10 full-bbox thermal
# request (~1.2k pts × 121 frames × 36 fields ≈ 5.3M) stays comfortably under this.
_MAX_RESPONSE_CELLS = 10_000_000

_DEFAULT_BBOX = f"{GRID_LAT_MIN},{GRID_LAT_MAX},{GRID_LON_MIN},{GRID_LON_MAX}"


def _err(code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


def _budget_error(endpoint: str, n_pts: int, n_frames: int, n_fields: int) -> JSONResponse | None:
    cells = n_pts * n_frames * n_fields
    if cells <= _MAX_RESPONSE_CELLS:
        return None
    logger.warning(
        "%s response rejected: %d pts × %d frames × %d fields = %d values > cap %d",
        endpoint, n_pts, n_frames, n_fields, cells, _MAX_RESPONSE_CELLS,
    )
    return _err(
        "response_too_large",
        f"Requested grid response is too large ({cells:,} values, cap {_MAX_RESPONSE_CELLS:,}). "
        "Increase stride_km or request a smaller bbox.",
        400,
    )


def _to_nullable(row: np.ndarray) -> list[float | None]:
    vals = np.round(row.astype(np.float64), 1).tolist()
    return [None if math.isnan(v) else v for v in vals]


@router.get("/station", response_model=StationForecastResponse)
async def station_forecast(
    station_id: str = Query(..., description="Station identifier, e.g. meteoswiss-BER"),
    hours: int = Query(120, ge=1, le=120, description="Forecast horizon (1–120 h)"),
) -> StationForecastResponse:
    """Return surface-weather ensemble forecast for one station.

    Params: station_id (str), hours (int, 1–120, default 120).
    Response: StationForecastResponse — wind km/h, temp °C, pressure hPa, precip mm.
    Errors: 404 unknown station, 503 cache warming.
    """
    data = get_station_forecast(station_id)
    if data is None:
        if cache_is_warm():
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": "station_not_found", "message": f"Unknown station: {station_id}"}},
            )
        raise HTTPException(
            status_code=503,
            detail={"error": {"code": "cache_warming", "message": "Forecast not yet available — cache warming in progress"}},
        )
    if hours < len(data.forecast):
        data = data.model_copy(update={"forecast": data.forecast[:hours]})
    return data


@router.get("/altitude-winds", response_model=AltitudeWindsResponse)
async def altitude_winds(
    station_id: str = Query(..., description="Station identifier"),
    hours: int = Query(120, ge=1, le=120, description="Forecast horizon (1–120 h)"),
) -> AltitudeWindsResponse:
    """Return vertical wind profiles (500–5000 m ASL) for one station.

    Params: station_id (str), hours (int, 1–120, default 120).
    Response: AltitudeWindsResponse — wind km/h, vertical wind m/s, 9 levels per profile.
    Errors: 404 unknown station, 503 cache warming.
    """
    data = get_station_altitude_winds(station_id)
    if data is None:
        if cache_is_warm():
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": "station_not_found", "message": f"Unknown station: {station_id}"}},
            )
        raise HTTPException(
            status_code=503,
            detail={"error": {"code": "cache_warming", "message": "Altitude-wind forecast not yet available — cache warming in progress"}},
        )
    if hours < len(data.profiles):
        data = data.model_copy(update={"profiles": data.profiles[:hours]})
    return data


@router.get("/grid")
async def wind_grid(
    level_m: int = Query(..., description="Altitude ASL in metres. Accepted: 500,1000,1500,2000,2500,3000,4000,5000"),
    bbox: str = Query(_DEFAULT_BBOX, description="lat_min,lat_max,lon_min,lon_max"),
    stride_km: int = Query(10, description="Grid spacing in km. Accepted: 1,2,5,10"),
) -> JSONResponse:
    """Return gridded ICON-CH1 wind forecast for a bbox and altitude level.

    Params: level_m (int), bbox (str, default Switzerland), stride_km (int, default 10).
    Response: GridForecastResponse — ws km/h, wd degrees, one frame per forecast hour.
    Errors: 400 bad params, 503 cache warming.
    """
    if level_m not in _VALID_GRID_LEVELS:
        return _err(
            "invalid_level",
            f"level_m must be one of {sorted(_VALID_GRID_LEVELS)}",
            400,
        )
    if stride_km not in _VALID_STRIDE_KM:
        return _err(
            "invalid_stride",
            f"stride_km must be one of {sorted(_VALID_STRIDE_KM)}",
            400,
        )

    try:
        parts = [float(x) for x in bbox.split(",")]
        if len(parts) != 4:
            raise ValueError
        lat_min, lat_max, lon_min, lon_max = parts
        if lat_min >= lat_max or lon_min >= lon_max:
            raise ValueError
        if not (_DOMAIN_LAT_MIN <= lat_min and lat_max <= _DOMAIN_LAT_MAX):
            raise ValueError
        if not (_DOMAIN_LON_MIN <= lon_min and lon_max <= _DOMAIN_LON_MAX):
            raise ValueError
    except (ValueError, TypeError):
        return _err(
            "invalid_bbox",
            "bbox must be 'lat_min,lat_max,lon_min,lon_max' within the ICON-CH1 domain "
            f"(lat {_DOMAIN_LAT_MIN}–{_DOMAIN_LAT_MAX}, lon {_DOMAIN_LON_MIN}–{_DOMAIN_LON_MAX})",
            400,
        )

    grid_cache = get_grid_wind_cache()
    if grid_cache is None or level_m not in grid_cache.ws:
        return _err("cache_warming", "Grid forecast not yet available — cache warming in progress", 503)

    # Generate the requested regular lat/lon grid (lat descending, lon ascending)
    req_step = stride_km / 111.0
    req_lats = np.arange(lat_max, lat_min - req_step / 2, -req_step)
    req_lons = np.arange(lon_min, lon_max + req_step / 2, req_step)
    lon_grid, lat_grid = np.meshgrid(req_lons, req_lats)
    flat_req_lats = lat_grid.ravel()
    flat_req_lons = lon_grid.ravel()
    n_pts = len(flat_req_lats)

    # Map each requested point to the nearest pre-sampled 1 km cache cell
    ws_arr = grid_cache.ws[level_m]  # (n_frames, N)
    wd_arr = grid_cache.wd[level_m]  # (n_frames, N)
    rh_arr = grid_cache.rh           # (n_frames, N)
    n_frames = ws_arr.shape[0]

    budget_err = _budget_error("wind-grid", n_pts, n_frames, 3)
    if budget_err is not None:
        return budget_err

    lat_indices = np.clip(
        np.round((grid_cache.lat_max - flat_req_lats) / grid_cache.step_deg).astype(int),
        0, grid_cache.n_lat - 1,
    )
    lon_indices = np.clip(
        np.round((flat_req_lons - grid_cache.lon_min) / grid_cache.step_deg).astype(int),
        0, grid_cache.n_lon - 1,
    )
    flat_cache_indices = lat_indices * grid_cache.n_lon + lon_indices

    grid_points = [
        {"lat": round(float(flat_req_lats[i]), 5), "lon": round(float(flat_req_lons[i]), 5)}
        for i in range(n_pts)
    ]

    frames = [
        {
            "valid_time": valid_time.isoformat(),
            "ws": _to_nullable(ws_arr[f_idx, flat_cache_indices]),
            "wd": _to_nullable(wd_arr[f_idx, flat_cache_indices]),
            "rh": _to_nullable(rh_arr[f_idx, flat_cache_indices]),
        }
        for f_idx, valid_time in enumerate(grid_cache.valid_times)
    ]

    return JSONResponse({
        "init_time": grid_cache.init_time.isoformat(),
        "model": grid_cache.model,
        "stride_km": stride_km,
        "grid": grid_points,
        "frames": frames,
    })


@router.get("/thermal-grid")
async def thermal_grid(
    bbox: str = Query(_DEFAULT_BBOX, description="lat_min,lat_max,lon_min,lon_max"),
    stride_km: int = Query(10, description="Grid spacing in km. Accepted: 1,2,5,10"),
) -> JSONResponse:
    """Return gridded thermal forecast (solar, CAPE, CIN, cloud cover, freezing level, etc.).

    Params: bbox (str, default Switzerland), stride_km (int, default 10).
    Response: ThermalGridResponse — one frame per forecast hour, values parallel to grid list.
    All values are ensemble medians. NaN fields are returned as null.
    Errors: 400 bad params, 503 cache warming.
    """
    if stride_km not in _VALID_STRIDE_KM:
        return _err("invalid_stride", f"stride_km must be one of {sorted(_VALID_STRIDE_KM)}", 400)

    try:
        parts = [float(x) for x in bbox.split(",")]
        if len(parts) != 4:
            raise ValueError
        lat_min, lat_max, lon_min, lon_max = parts
        if lat_min >= lat_max or lon_min >= lon_max:
            raise ValueError
        if not (_DOMAIN_LAT_MIN <= lat_min and lat_max <= _DOMAIN_LAT_MAX):
            raise ValueError
        if not (_DOMAIN_LON_MIN <= lon_min and lon_max <= _DOMAIN_LON_MAX):
            raise ValueError
    except (ValueError, TypeError):
        return _err(
            "invalid_bbox",
            "bbox must be 'lat_min,lat_max,lon_min,lon_max' within the ICON-CH1 domain "
            f"(lat {_DOMAIN_LAT_MIN}–{_DOMAIN_LAT_MAX}, lon {_DOMAIN_LON_MIN}–{_DOMAIN_LON_MAX})",
            400,
        )

    thermal_cache = get_thermal_grid_cache()
    if thermal_cache is None:
        return _err("cache_warming", "Thermal grid forecast not yet available — cache warming in progress", 503)

    # Generate requested regular lat/lon grid
    req_step = stride_km / 111.0
    req_lats = np.arange(lat_max, lat_min - req_step / 2, -req_step)
    req_lons = np.arange(lon_min, lon_max + req_step / 2, req_step)
    lon_grid, lat_grid = np.meshgrid(req_lons, req_lats)
    flat_req_lats = lat_grid.ravel()
    flat_req_lons = lon_grid.ravel()
    n_pts = len(flat_req_lats)

    _BASE = ("solar", "sunshine", "cloud_cover", "cloud_low", "cloud_mid", "cloud_high",
             "freezing_level", "cape", "cin", "lcl", "lfc", "tke")
    _fields = _BASE + tuple(f"{f}_min" for f in _BASE) + tuple(f"{f}_max" for f in _BASE)

    n_frames = len(thermal_cache.valid_times)
    budget_err = _budget_error("thermal-grid", n_pts, n_frames, len(_fields))
    if budget_err is not None:
        return budget_err

    # Map each requested point to the nearest pre-sampled 1 km cache cell
    lat_indices = np.clip(
        np.round((thermal_cache.lat_max - flat_req_lats) / thermal_cache.step_deg).astype(int),
        0, thermal_cache.n_lat - 1,
    )
    lon_indices = np.clip(
        np.round((flat_req_lons - thermal_cache.lon_min) / thermal_cache.step_deg).astype(int),
        0, thermal_cache.n_lon - 1,
    )
    flat_cache_indices = lat_indices * thermal_cache.n_lon + lon_indices

    grid_points = [
        {"lat": round(float(flat_req_lats[i]), 5), "lon": round(float(flat_req_lons[i]), 5)}
        for i in range(n_pts)
    ]

    frames = []
    for f_idx, valid_time in enumerate(thermal_cache.valid_times):
        frame = {"valid_time": valid_time.isoformat()}
        for f in _fields:
            frame[f] = _to_nullable(getattr(thermal_cache, f)[f_idx, flat_cache_indices])
        frames.append(frame)

    return JSONResponse({
        "init_time": thermal_cache.init_time.isoformat(),
        "model": thermal_cache.model,
        "stride_km": stride_km,
        "grid": grid_points,
        "frames": frames,
    })
