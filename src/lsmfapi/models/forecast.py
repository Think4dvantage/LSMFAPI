import math
from dataclasses import dataclass
from datetime import datetime

import numpy as np
from pydantic import BaseModel, field_validator


class EnsembleValue(BaseModel):
    """Internal — used during collection, not in API responses."""
    probable: float | None
    min: float | None
    max: float | None

    @field_validator("probable", "min", "max", mode="before")
    @classmethod
    def nan_to_none(cls, v: object) -> float | None:
        if isinstance(v, float) and math.isnan(v):
            return None
        return v


# ---------- Station forecast ----------

class StationForecastHour(BaseModel):
    valid_time: datetime

    wind_speed: float | None        # km/h at 10 m
    wind_speed_min: float | None
    wind_speed_max: float | None
    wind_gust: float | None         # km/h at 10 m (max in step)
    wind_gust_min: float | None
    wind_gust_max: float | None
    wind_direction: float | None    # degrees, met. convention (0=N, 90=E)
    wind_direction_min: float | None
    wind_direction_max: float | None
    temperature: float | None       # °C
    temperature_min: float | None
    temperature_max: float | None
    humidity: float | None          # %
    humidity_min: float | None
    humidity_max: float | None
    pressure_qff: float | None      # hPa sea-level
    pressure_qff_min: float | None
    pressure_qff_max: float | None
    precipitation: float | None     # mm
    precipitation_min: float | None
    precipitation_max: float | None


class StationForecastResponse(BaseModel):
    """GET /api/forecast/station — errors: 404 unknown station, 503 cache warming."""
    station_id: str
    init_time: datetime     # model run initialisation time UTC
    model: str
    source: str
    forecast: list[StationForecastHour]


# ---------- Altitude winds ----------

class AltitudeWindLevel(BaseModel):
    level_m: int

    wind_speed: float | None        # km/h
    wind_speed_min: float | None
    wind_speed_max: float | None
    wind_direction: float | None    # degrees
    wind_direction_min: float | None
    wind_direction_max: float | None
    vertical_wind: float | None     # m/s, positive = upward
    vertical_wind_min: float | None
    vertical_wind_max: float | None


class AltitudeWindsProfile(BaseModel):
    valid_time: datetime
    levels: list[AltitudeWindLevel]


class AltitudeWindsResponse(BaseModel):
    """GET /api/forecast/altitude-winds — errors: 404 unknown station, 503 cache warming."""
    station_id: str
    init_time: datetime
    model: str
    source: str
    profiles: list[AltitudeWindsProfile]


# ---------- Grid forecast ----------

class GridPoint(BaseModel):
    lat: float
    lon: float


class GridFrame(BaseModel):
    valid_time: datetime
    ws: list[float | None]  # km/h, parallel to grid
    wd: list[float | None]  # degrees, parallel to grid
    rh: list[float | None]  # % surface relative humidity, parallel to grid


class GridForecastResponse(BaseModel):
    """GET /api/forecast/grid — errors: 400 bad params, 503 cache warming."""
    init_time: datetime
    model: str
    stride_km: int
    grid: list[GridPoint]
    frames: list[GridFrame]


# ---------- Internal: grid wind cache (not persisted) ----------

@dataclass
class GridWindCache:
    """Pre-sampled 1 km regular grid over the default Switzerland bbox.

    lats/lons are row-major (lat descending, lon ascending).
    ws/wd keyed by level_m → (n_frames, N) arrays, km/h / degrees.
    """
    model: str               # "icon-ch1" or "icon-ch2"
    init_time: datetime
    lats: np.ndarray        # shape (N,)
    lons: np.ndarray        # shape (N,)
    n_lat: int
    n_lon: int
    lat_max: float
    lon_min: float
    step_deg: float
    valid_times: list[datetime]
    ws: dict[int, np.ndarray]   # level_m → (n_frames, N)
    wd: dict[int, np.ndarray]   # level_m → (n_frames, N)
    rh: np.ndarray              # (n_frames, N) surface relative humidity %
