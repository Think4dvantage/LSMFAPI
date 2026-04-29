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
    GRID_STEP_DEG,
)
from lsmfapi.database.cache import (
    cache_is_warm,
    get_grid_wind_cache,
    get_station_altitude_winds,
    get_station_forecast,
    known_stations,
)
from lsmfapi.models.forecast import (
    AltitudeWindsResponse,
    GridForecastResponse,
    GridFrame,
    GridPoint,
    StationForecastResponse,
)

router = APIRouter(prefix="/api/forecast", tags=["forecast"])

# Accepted parameter sets for the grid endpoint
_VALID_GRID_LEVELS: frozenset[int] = frozenset(ALTITUDE_TO_HPA.keys()) - {800}
_VALID_STRIDE_KM: frozenset[int] = frozenset({1, 2, 5, 10})

# ICON-CH1 domain envelope used for bbox validation
_DOMAIN_LAT_MIN, _DOMAIN_LAT_MAX = 43.0, 50.0
_DOMAIN_LON_MIN, _DOMAIN_LON_MAX = 3.0, 17.0

_DEFAULT_BBOX = f"{GRID_LAT_MIN},{GRID_LAT_MAX},{GRID_LON_MIN},{GRID_LON_MAX}"


def _err(code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


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
    n_frames = ws_arr.shape[0]
    n_cache_lat = grid_cache.n_lat
    n_cache_lon = grid_cache.n_lon

    lat_indices = np.clip(
        np.round((grid_cache.lat_max - flat_req_lats) / grid_cache.step_deg).astype(int),
        0, n_cache_lat - 1,
    )
    lon_indices = np.clip(
        np.round((flat_req_lons - grid_cache.lon_min) / grid_cache.step_deg).astype(int),
        0, n_cache_lon - 1,
    )
    flat_cache_indices = lat_indices * n_cache_lon + lon_indices

    grid_points = [
        GridPoint(lat=round(float(flat_req_lats[i]), 5), lon=round(float(flat_req_lons[i]), 5))
        for i in range(n_pts)
    ]

    rh_arr = grid_cache.rh  # (n_frames, N)

    def _to_nullable(row: np.ndarray) -> list[float | None]:
        return [None if math.isnan(float(v)) else round(float(v), 1) for v in row]

    frames = []
    for f_idx, valid_time in enumerate(grid_cache.valid_times):
        frames.append(GridFrame(
            valid_time=valid_time,
            ws=_to_nullable(ws_arr[f_idx, flat_cache_indices]),
            wd=_to_nullable(wd_arr[f_idx, flat_cache_indices]),
            rh=_to_nullable(rh_arr[f_idx, flat_cache_indices]),
        ))

    response = GridForecastResponse(
        init_time=grid_cache.init_time,
        model=grid_cache.model,
        stride_km=stride_km,
        grid=grid_points,
        frames=frames,
    )
    return JSONResponse(response.model_dump(mode="json"))
