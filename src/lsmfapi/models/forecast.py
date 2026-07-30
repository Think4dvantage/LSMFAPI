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


# ---------- Internal: thermal grid cache ----------

@dataclass
class ThermalGridCache:
    """Pre-sampled ~1 km regular grid thermal forecast over the default Switzerland bbox.

    All arrays are shape (n_frames, N) where N = n_lat × n_lon (row-major).
    Values are ensemble medians (float16). NaN encodes missing / fill-value data.
    """
    model: str               # "icon-ch1" or "icon-ch2"
    init_time: datetime
    lats: np.ndarray         # shape (N,)
    lons: np.ndarray         # shape (N,)
    n_lat: int
    n_lon: int
    lat_max: float
    lon_min: float
    step_deg: float
    valid_times: list[datetime]
    solar: np.ndarray               # (n_frames, N) W/m² — median
    solar_min: np.ndarray
    solar_max: np.ndarray
    sunshine: np.ndarray            # (n_frames, N) min/h — median
    sunshine_min: np.ndarray
    sunshine_max: np.ndarray
    cloud_cover: np.ndarray         # (n_frames, N) % — median
    cloud_cover_min: np.ndarray
    cloud_cover_max: np.ndarray
    cloud_low: np.ndarray           # (n_frames, N) % — median
    cloud_low_min: np.ndarray
    cloud_low_max: np.ndarray
    cloud_mid: np.ndarray           # (n_frames, N) % — median
    cloud_mid_min: np.ndarray
    cloud_mid_max: np.ndarray
    cloud_high: np.ndarray          # (n_frames, N) % — median
    cloud_high_min: np.ndarray
    cloud_high_max: np.ndarray
    freezing_level: np.ndarray      # (n_frames, N) m ASL — median
    freezing_level_min: np.ndarray
    freezing_level_max: np.ndarray
    cape: np.ndarray                # (n_frames, N) J/kg — median
    cape_min: np.ndarray
    cape_max: np.ndarray
    cin: np.ndarray                 # (n_frames, N) J/kg (NaN where ICON fill) — median
    cin_min: np.ndarray
    cin_max: np.ndarray
    lcl: np.ndarray                 # (n_frames, N) m — median
    lcl_min: np.ndarray
    lcl_max: np.ndarray
    lfc: np.ndarray                 # (n_frames, N) m — median
    lfc_min: np.ndarray
    lfc_max: np.ndarray
    tke: np.ndarray                 # (n_frames, N) J/kg — median
    tke_min: np.ndarray
    tke_max: np.ndarray


# ---------- Internal: grid wind cache ----------

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
