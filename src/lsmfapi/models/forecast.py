from datetime import datetime

from pydantic import BaseModel


class EnsembleValue(BaseModel):
    probable: float  # median across all members × runs
    min: float       # absolute minimum
    max: float       # absolute maximum


class PressureLevelWinds(BaseModel):
    altitude_m: int           # m ASL (see altitude level mapping in architecture.md)
    wind_speed: EnsembleValue
    wind_direction: EnsembleValue
    vertical_wind: EnsembleValue  # m/s — positive = updraft, negative = downdraft


class ForecastPoint(BaseModel):
    valid_time: datetime

    # --- Surface winds ---
    wind_speed: EnsembleValue        # m/s at 10m
    wind_gusts: EnsembleValue        # m/s at 10m (max gust in step)
    wind_direction: EnsembleValue    # degrees (0/360 = N, 90 = E)

    # --- Surface conditions ---
    temperature: EnsembleValue       # °C
    humidity: EnsembleValue          # % relative humidity
    pressure_qff: EnsembleValue      # hPa reduced to sea level (QFF)
    precipitation: EnsembleValue     # mm/h (de-accumulated from TOT_PREC)

    # --- Radiation ---
    solar_direct: EnsembleValue      # W/m² mean over hour (de-accumulated ASWDIR_S)
    solar_diffuse: EnsembleValue     # W/m² mean over hour (de-accumulated ASWDIFD_S)
    sunshine_minutes: EnsembleValue  # minutes of sunshine in the hour (de-accumulated DURSUN)

    # --- Cloud ---
    cloud_cover_total: EnsembleValue      # % (0–100)
    cloud_cover_low: EnsembleValue        # %
    cloud_cover_mid: EnsembleValue        # %
    cloud_cover_high: EnsembleValue       # %
    cloud_base_convective: EnsembleValue  # m AGL; 0 when no convective cloud (HBAS_CON)

    # --- Thermics / convection ---
    boundary_layer_height: EnsembleValue  # m AGL — proxy for thermal ceiling (HPBL)
    freezing_level: EnsembleValue         # m ASL — height of 0 °C isotherm (HZEROCL)
    cape: EnsembleValue                   # J/kg — convective energy; >500 = significant (CAPE_ML)
    cin: EnsembleValue                    # J/kg — convective inhibition; negative (CIN_ML)

    # --- Pressure-level winds (9 altitude bands: 500–5000m ASL) ---
    pressure_levels: list[PressureLevelWinds]


class ForecastResponse(BaseModel):
    station_lat: float
    station_lon: float
    station_elevation: int
    generated_at: datetime
    hours: list[ForecastPoint]


class GridForecastPoint(BaseModel):
    lat: float
    lon: float
    ws: list[float]      # probable wind speed per hour (m/s)
    ws_min: list[float]
    ws_max: list[float]
    wd: list[float]      # probable wind direction per hour (degrees)
    wd_min: list[float]
    wd_max: list[float]
    wv: list[float]      # probable vertical wind per hour (m/s; positive = updraft)
    wv_min: list[float]
    wv_max: list[float]


class GridResponse(BaseModel):
    date: str       # YYYY-MM-DD
    level_m: int    # altitude level in m ASL
    generated_at: datetime
    points: list[GridForecastPoint]
