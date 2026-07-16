import asyncio
import logging
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import eccodes
import httpx
import numpy as np
from scipy.spatial import cKDTree

from lsmfapi.collectors.base import BaseCollector
from lsmfapi.collectors.grib_cache import grib_run_dir
from lsmfapi.config import get_config
from lsmfapi.database.cache import set_grid_wind_cache, set_station_altitude_winds, set_station_forecast, set_thermal_grid_cache
from lsmfapi.database import collection_state as _cs
from lsmfapi.database import telemetry as _telemetry
from lsmfapi.models.forecast import (
    AltitudeWindLevel,
    AltitudeWindsProfile,
    AltitudeWindsResponse,
    EnsembleValue,
    GridWindCache,
    StationForecastHour,
    StationForecastResponse,
    ThermalGridCache,
)
from lsmfapi.services.ensemble import compute_stats, compute_wind_direction_stats

logger = logging.getLogger(__name__)

# ---------- Module-level grid singleton (built once per process) ----------
_GRID_TREE: cKDTree | None = None
_GRID_LATS: np.ndarray | None = None   # all ICON-CH1 grid lats
_GRID_LONS: np.ndarray | None = None   # all ICON-CH1 grid lons

# Pre-sampled 1 km regular grid indices into _GRID_TREE for the default bbox
_GRID_SAMPLE_INDICES: np.ndarray | None = None
_GRID_N_LAT: int = 0
_GRID_N_LON: int = 0

# Default Switzerland bbox for grid pre-sampling
GRID_LAT_MAX = 47.9
GRID_LAT_MIN = 45.8
GRID_LON_MIN = 5.9
GRID_LON_MAX = 10.6
GRID_STEP_DEG = 1.0 / 111.0  # ~1 km

# ---------- Collection constants ----------
COLLECTION = "ch.meteoschweiz.ogd-forecasting-icon-ch1"
N_MEMBERS = 11  # informational only — actual count is read from each GRIB run
HORIZONS = list(range(34))  # 0 h … 33 h inclusive

SURFACE_VARS: list[str] = [
    "U_10M", "V_10M", "VMAX_10M",
    "T_2M", "TD_2M", "PMSL",
    "TOT_PREC", "DURSUN", "ASWDIR_S", "ASWDIFD_S",
    "CLCT", "CLCL", "CLCM", "CLCH",
    "HZEROCL", "CAPE_ML", "CIN_ML",
    "LCL_ML", "LFC_ML", "TKE",
]
ACCUM_VARS: frozenset[str] = frozenset({"TOT_PREC", "DURSUN", "ASWDIR_S", "ASWDIFD_S"})

PRESSURE_VARS: list[str] = ["U", "V", "W"]   # full set used by CH2
CH1_PRESSURE_VARS: list[str] = ["U", "V", "W"]  # W confirmed available in CH1 STAC catalog at same pressure levels as U/V
ALTITUDE_TO_HPA: dict[int, int] = {
    500: 950, 800: 920, 1000: 900, 1500: 850, 2000: 800,
    2500: 750, 3000: 700, 4000: 600, 5000: 500,
}

DOWNLOAD_CONCURRENCY = 6


# ---------- Pure helpers ----------

def _horizon_str(h: int) -> str:
    return f"P0DT{h:02d}H00M00S"


def _parse_horizon_h(s: str) -> int:
    """Parse ISO 8601 duration → integer hours. Returns -1 on failure."""
    m = re.match(r"P(\d+)DT(\d+)H", s)
    if m:
        return int(m.group(1)) * 24 + int(m.group(2))
    m = re.match(r"PT(\d+)H", s)
    if m:
        return int(m.group(1))
    return -1


def _f(v: float | None, scale: float = 1.0) -> float | None:
    """Scale a nullable float and round to 1 dp; pass through None."""
    if v is None:
        return None
    result = v * scale
    return None if math.isnan(result) else round(result, 1)


def _ev_flat(ev: EnsembleValue, scale: float = 1.0) -> tuple[float | None, float | None, float | None]:
    """Return (probable, min, max) from EnsembleValue, optionally scaled."""
    return _f(ev.probable, scale), _f(ev.min, scale), _f(ev.max, scale)


async def _search_item_url(
    client: httpx.AsyncClient,
    stac_base_url: str,
    collection: str,
    ref_dt: datetime,
    variable: str,
    horizon_h: int,
    perturbed: bool = True,
) -> str | None:
    payload = {
        "collections": [collection],
        "forecast:reference_datetime": f"{ref_dt.strftime('%Y-%m-%dT%H:%M:%SZ')}/..",
        "forecast:variable": variable,
        "forecast:perturbed": perturbed,
        "forecast:horizon": _horizon_str(horizon_h),
    }
    resp = await client.post(f"{stac_base_url}/search", json=payload, timeout=30)
    resp.raise_for_status()
    features = resp.json().get("features", [])
    if not features:
        return None
    assets = features[0].get("assets") or {}
    if not assets:
        return None
    return next(iter(assets.values())).get("href")


def _latest_ref_dt() -> datetime:
    """Return most recent CH1-EPS run time likely already published (2 h guard)."""
    now = datetime.now(timezone.utc)
    hour = (now.hour // 6) * 6
    candidate = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if (now - candidate).total_seconds() < 2 * 3600:
        candidate -= timedelta(hours=6)
    return candidate


def _deaccumulate(arr: np.ndarray) -> np.ndarray:
    """Difference accumulated field along steps axis (axis=0)."""
    return np.diff(arr, axis=0, prepend=arr[:1, :] * 0)


def _compute_rh_from_td(t_k: np.ndarray, td_k: np.ndarray) -> np.ndarray:
    """Relative humidity from T_2M and TD_2M (Kelvin), Magnus formula."""
    t_c  = t_k  - 273.15
    td_c = td_k - 273.15
    rh = 100.0 * np.exp(17.625 * td_c / (243.04 + td_c)) / np.exp(17.625 * t_c / (243.04 + t_c))
    return np.clip(rh, 0.0, 100.0)


def _to_ensemble_value(arr_1d: np.ndarray) -> EnsembleValue:
    stats = compute_stats(arr_1d.tolist())
    return EnsembleValue(**stats)


def _wind_ensemble_value(u: np.ndarray, v: np.ndarray) -> tuple[EnsembleValue, EnsembleValue]:
    speeds = np.sqrt(u ** 2 + v ** 2)
    directions = (270.0 - np.degrees(np.arctan2(v, u))) % 360.0
    return (
        EnsembleValue(**compute_stats(speeds.tolist())),
        EnsembleValue(**compute_wind_direction_stats(directions.tolist())),
    )


# ---------- eccodes-based GRIB2 readers ----------

def _eccodes_get(msg, key: str, default=None):
    try:
        return eccodes.codes_get(msg, key)
    except eccodes.CodesInternalError:
        return default


def _read_grid_coords(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """Read geographic lat/lon from a COSMO/ICON horizontal_constants GRIB2."""
    rlat: np.ndarray | None = None
    rlon: np.ndarray | None = None

    found_names: list[str] = []
    with open(str(path), "rb") as f:
        while True:
            msg = eccodes.codes_grib_new_from_file(f)
            if msg is None:
                break
            try:
                sn = _eccodes_get(msg, "shortName", default="<unknown>")
                found_names.append(sn)
                if sn == "CLAT" and rlat is None:
                    rlat = eccodes.codes_get_array(msg, "values").astype(np.float64)
                elif sn == "CLON" and rlon is None:
                    rlon = eccodes.codes_get_array(msg, "values").astype(np.float64)
            finally:
                eccodes.codes_release(msg)
            if rlat is not None and rlon is not None:
                break

    logger.info("Constants file messages (shortName): %s", found_names)

    if rlat is None or rlon is None:
        raise RuntimeError(
            f"CLAT/CLON messages not found in {path.name}. "
            f"Messages found: {found_names}"
        )

    if np.max(np.abs(rlat)) <= np.pi / 2 + 0.01:
        lats = np.degrees(rlat)
        lons = np.degrees(rlon)
    else:
        lats, lons = rlat, rlon

    logger.info(
        "Grid coords: lat [%.3f, %.3f]  lon [%.3f, %.3f]  (%d points)",
        lats.min(), lats.max(), lons.min(), lons.max(), len(lats),
    )
    return lats, lons


def _approx_hybrid_to_pressure_hpa(
    unique_levels: list[int],
    pv: np.ndarray,
    p_surf_pa: float = 101325.0,
) -> np.ndarray:
    """Convert ICON generalVerticalLayer indices to approximate pressure in hPa.

    ICON hybrid coordinate: p(k) = 0.5*[(ak[k-1]+bk[k-1]*p_surf)+(ak[k]+bk[k]*p_surf)]
    pv layout: [ak_0 ... ak_N, bk_0 ... bk_N]  (N+1 half-levels, Pa / dimensionless).
    """
    n_half = len(pv) // 2
    ak = pv[:n_half]
    bk = pv[n_half:]
    result = []
    for lvl in unique_levels:
        k = int(lvl)
        if 1 <= k <= n_half - 1:
            p_above = ak[k - 1] + bk[k - 1] * p_surf_pa
            p_below = ak[k]     + bk[k]     * p_surf_pa
            result.append(0.5 * (p_above + p_below) / 100.0)
        else:
            result.append(float(lvl))  # fallback: treat as hPa
    return np.array(result, dtype=float)


def _build_level_indices(level_hpa: np.ndarray) -> dict[int, int]:
    """Map each altitude target pressure (ALTITUDE_TO_HPA values) to nearest level index.

    Uses nearest-match so it works for both isobaricInhPa (exact values) and
    generalVerticalLayer (approximate pressures from hybrid coordinate conversion).
    """
    result = {}
    for target_hpa in ALTITUDE_TO_HPA.values():
        idx = int(np.argmin(np.abs(level_hpa - target_hpa)))
        result[target_hpa] = idx
    return result


def _read_grib2_eccodes(
    path: Path,
    extract_indices: np.ndarray | None = None,
) -> tuple[np.ndarray | None, np.ndarray | None]:
    """Read a forecast GRIB2 file with eccodes, returning (values, level_hpa).

    Two-pass read: pass 1 collects metadata only; pass 2 fills a pre-allocated
    array directly, one message at a time. This avoids accumulating all message
    arrays in memory simultaneously before building the output array.

    When extract_indices is provided, only those grid-point indices are stored.
    values shape without extract_indices:
      Surface  : (n_members, n_points)
      Multi-lev: (n_members, n_levels, n_points)
    values shape with extract_indices:
      Surface  : (n_members, len(extract_indices))
      Multi-lev: (n_members, n_levels, len(extract_indices))
    level_hpa  : None for surface, ndarray of hPa for pressure-level files.
                 For generalVerticalLayer files the hPa values are approximated
                 from the embedded hybrid (pv) coordinate at standard sea-level
                 pressure — use _build_level_indices() for a nearest-match lookup.
    """
    # Pass 1: metadata scan — no values arrays stored
    unique_members: set[int] = set()
    unique_levels: set[int] = set()
    n_points: int = 0
    _level_type: str = ""
    _pv: np.ndarray | None = None

    try:
        with open(str(path), "rb") as f:
            while True:
                msg = eccodes.codes_grib_new_from_file(f)
                if msg is None:
                    break
                try:
                    unique_members.add(int(_eccodes_get(msg, "perturbationNumber", default=0)))
                    unique_levels.add(int(_eccodes_get(msg, "level", default=0)))
                    if n_points == 0:
                        n_points = eccodes.codes_get_size(msg, "values")
                    if not _level_type:
                        _level_type = _eccodes_get(msg, "typeOfLevel", default="") or ""
                        if _level_type == "generalVerticalLayer" and _pv is None:
                            try:
                                _pv = eccodes.codes_get_array(msg, "pv").astype(np.float64)
                            except Exception:
                                pass
                finally:
                    eccodes.codes_release(msg)
    except Exception as exc:
        logger.error("eccodes read failed for %s: %s", path.name, exc)
        raise

    if not unique_members or n_points == 0:
        logger.warning("No GRIB2 messages in %s", path.name)
        return None, None

    sorted_members = sorted(unique_members)
    sorted_levels  = sorted(unique_levels)
    is_surface = len(sorted_levels) == 1

    member_idx = {m: i for i, m in enumerate(sorted_members)}
    level_idx  = {l: i for i, l in enumerate(sorted_levels)}

    n_out = len(extract_indices) if extract_indices is not None else n_points
    if is_surface:
        arr = np.full((len(sorted_members), n_out), np.nan, dtype=np.float32)
    else:
        arr = np.full((len(sorted_members), len(sorted_levels), n_out), np.nan, dtype=np.float32)

    # Pass 2: fill arr directly — each message's values array is released immediately
    try:
        with open(str(path), "rb") as f:
            while True:
                msg = eccodes.codes_grib_new_from_file(f)
                if msg is None:
                    break
                try:
                    member = int(_eccodes_get(msg, "perturbationNumber", default=0))
                    level  = int(_eccodes_get(msg, "level", default=0))
                    values = eccodes.codes_get_array(msg, "values").astype(np.float32)
                    if extract_indices is not None:
                        values = values[extract_indices]
                    mi = member_idx[member]
                    if is_surface:
                        arr[mi] = values
                    else:
                        arr[mi, level_idx[level]] = values
                finally:
                    eccodes.codes_release(msg)
    except Exception as exc:
        logger.error("eccodes read failed for %s: %s", path.name, exc)
        raise

    if not is_surface:
        if _level_type == "generalVerticalLayer" and _pv is not None:
            level_coords = _approx_hybrid_to_pressure_hpa(sorted_levels, _pv)
            logger.debug(
                "generalVerticalLayer: %d levels, approx hPa range [%.0f, %.0f]",
                len(level_coords), level_coords.min(), level_coords.max(),
            )
        else:
            level_coords = np.array(sorted_levels, dtype=float)
        return arr, level_coords

    return arr, None


# ---------- Shared grid helper (used by CH1 and CH2 collectors) ----------

_THERMAL_ACCUM_VARS: frozenset[str] = frozenset({"ASWDIR_S", "ASWDIFD_S", "DURSUN"})
_THERMAL_SURFACE_VARS: list[str] = [
    "ASWDIR_S", "ASWDIFD_S", "DURSUN",
    "CLCT", "CLCL", "CLCM", "CLCH",
    "HZEROCL", "CAPE_ML", "CIN_ML", "LCL_ML", "LFC_ML", "TKE",
]
_CIN_FILL_THRESHOLD = -900.0


def _build_thermal_grid_cache(
    horizons: list[int],
    ref_dt: datetime,
    tmpdir: Path,
    sample_indices: np.ndarray,
    n_lat: int,
    n_lon: int,
    model: str,
    accum_prior_h: int | None = None,
) -> ThermalGridCache:
    """Build a ThermalGridCache from surface GRIB files kept on disk in tmpdir.

    Reads one horizon at a time. Accumulated fields (solar, sunshine) are de-accumulated
    by differencing consecutive steps. If accum_prior_h is given, loads that step as the
    initial accumulated baseline (needed for CH2 which starts at h=34).

    Files are NOT deleted — grib_run_dir() manages cleanup on the next collection run.
    """
    n_grid = len(sample_indices)
    n_horizons = len(horizons)
    # float16 storage: these are ensemble-median map-viz fields; every field's range
    # (cloud %, W/m², m, J/kg) sits well inside float16's ±65504 and its ~3 sig figs
    # exceed the data's real accuracy. Halves resident grid memory. Stats below are
    # computed in float64 and only cast to float16 on store.
    _nan = lambda: np.full((n_horizons, n_grid), np.nan, dtype=np.float16)  # noqa: E731

    solar_cache        = _nan(); solar_min_cache        = _nan(); solar_max_cache        = _nan()
    sunshine_cache     = _nan(); sunshine_min_cache     = _nan(); sunshine_max_cache     = _nan()
    cloud_cover_cache  = _nan(); cloud_cover_min_cache  = _nan(); cloud_cover_max_cache  = _nan()
    cloud_low_cache    = _nan(); cloud_low_min_cache    = _nan(); cloud_low_max_cache    = _nan()
    cloud_mid_cache    = _nan(); cloud_mid_min_cache    = _nan(); cloud_mid_max_cache    = _nan()
    cloud_high_cache   = _nan(); cloud_high_min_cache   = _nan(); cloud_high_max_cache   = _nan()
    freezing_level_cache = _nan(); freezing_level_min_cache = _nan(); freezing_level_max_cache = _nan()
    cape_cache         = _nan(); cape_min_cache         = _nan(); cape_max_cache         = _nan()
    cin_cache          = _nan(); cin_min_cache          = _nan(); cin_max_cache          = _nan()
    lcl_cache          = _nan(); lcl_min_cache          = _nan(); lcl_max_cache          = _nan()
    lfc_cache          = _nan(); lfc_min_cache          = _nan(); lfc_max_cache          = _nan()
    tke_cache          = _nan(); tke_min_cache          = _nan(); tke_max_cache          = _nan()

    def _read(var: str, h: int) -> np.ndarray | None:
        dest = tmpdir / f"{var}_{h:03d}.grib2"
        if not dest.exists():
            return None
        try:
            arr, _ = _read_grib2_eccodes(dest, extract_indices=sample_indices)
            if arr is None or arr.ndim < 2:
                return None
            if arr.ndim == 3:
                arr = arr[:, -1, :]  # take bottom model level
            return arr.astype(np.float64)
        except Exception as exc:
            logger.warning("ThermalGrid parse %s h=%d: %s", var, h, exc)
            return None

    def _median(arr: np.ndarray) -> np.ndarray:
        return np.nanmedian(arr, axis=0).astype(np.float16)

    def _nanmin(arr: np.ndarray) -> np.ndarray:
        return np.nanmin(arr, axis=0).astype(np.float16)

    def _nanmax(arr: np.ndarray) -> np.ndarray:
        return np.nanmax(arr, axis=0).astype(np.float16)

    # Load prior accumulated values for deaccumulation baseline
    prev_aswdir: np.ndarray | None = None
    prev_aswdifd: np.ndarray | None = None
    prev_dursun: np.ndarray | None = None
    if accum_prior_h is not None:
        prev_aswdir  = _read("ASWDIR_S",  accum_prior_h)
        prev_aswdifd = _read("ASWDIFD_S", accum_prior_h)
        prev_dursun  = _read("DURSUN",    accum_prior_h)

    for h_idx, h in enumerate(horizons):
        # --- accumulated: solar radiation ---
        raw_dir  = _read("ASWDIR_S",  h)
        raw_difd = _read("ASWDIFD_S", h)
        if raw_dir is not None and raw_difd is not None:
            delta_dir  = raw_dir  - (prev_aswdir  if prev_aswdir  is not None else 0.0)
            delta_difd = raw_difd - (prev_aswdifd if prev_aswdifd is not None else 0.0)
            solar_w_m2 = np.clip(delta_dir + delta_difd, 0.0, None) / 3600.0  # J/m² → W/m²
            solar_cache[h_idx]     = _median(solar_w_m2)
            solar_min_cache[h_idx] = _nanmin(solar_w_m2)
            solar_max_cache[h_idx] = _nanmax(solar_w_m2)
            prev_aswdir  = raw_dir
            prev_aswdifd = raw_difd

        # --- accumulated: sunshine ---
        raw_dursun = _read("DURSUN", h)
        if raw_dursun is not None:
            delta_dursun = raw_dursun - (prev_dursun if prev_dursun is not None else 0.0)
            sunshine_min = np.clip(delta_dursun, 0.0, None) / 60.0  # s → min
            sunshine_cache[h_idx]     = _median(sunshine_min)
            sunshine_min_cache[h_idx] = _nanmin(sunshine_min)
            sunshine_max_cache[h_idx] = _nanmax(sunshine_min)
            prev_dursun = raw_dursun

        # --- instantaneous surface fields ---
        for arr, med_cache, min_cache, max_cache in (
            (_read("CLCT",    h), cloud_cover_cache,   cloud_cover_min_cache,   cloud_cover_max_cache),
            (_read("CLCL",    h), cloud_low_cache,     cloud_low_min_cache,     cloud_low_max_cache),
            (_read("CLCM",    h), cloud_mid_cache,     cloud_mid_min_cache,     cloud_mid_max_cache),
            (_read("CLCH",    h), cloud_high_cache,    cloud_high_min_cache,    cloud_high_max_cache),
            (_read("HZEROCL", h), freezing_level_cache, freezing_level_min_cache, freezing_level_max_cache),
            (_read("LCL_ML",  h), lcl_cache,           lcl_min_cache,           lcl_max_cache),
            (_read("LFC_ML",  h), lfc_cache,           lfc_min_cache,           lfc_max_cache),
            (_read("TKE",     h), tke_cache,           tke_min_cache,           tke_max_cache),
        ):
            if arr is not None:
                med_cache[h_idx] = _median(arr)
                min_cache[h_idx] = _nanmin(arr)
                max_cache[h_idx] = _nanmax(arr)

        cape_arr = _read("CAPE_ML", h)
        if cape_arr is not None:
            cape_cache[h_idx]     = _median(cape_arr)
            cape_min_cache[h_idx] = _nanmin(cape_arr)
            cape_max_cache[h_idx] = _nanmax(cape_arr)

        cin_arr = _read("CIN_ML", h)
        if cin_arr is not None:
            cin_masked = np.where(cin_arr < _CIN_FILL_THRESHOLD, np.nan, cin_arr)
            cin_cache[h_idx]     = _median(cin_masked)
            cin_min_cache[h_idx] = _nanmin(cin_masked)
            cin_max_cache[h_idx] = _nanmax(cin_masked)

        logger.debug("ThermalGrid %s h=%d computed", model, h)

    lat_arr = np.arange(GRID_LAT_MAX, GRID_LAT_MIN - GRID_STEP_DEG / 2, -GRID_STEP_DEG)
    lon_arr = np.arange(GRID_LON_MIN, GRID_LON_MAX + GRID_STEP_DEG / 2, GRID_STEP_DEG)
    lon_grid_2d, lat_grid_2d = np.meshgrid(lon_arr, lat_arr)

    return ThermalGridCache(
        model=model,
        init_time=ref_dt,
        lats=lat_grid_2d.ravel().astype(np.float32),
        lons=lon_grid_2d.ravel().astype(np.float32),
        n_lat=n_lat,
        n_lon=n_lon,
        lat_max=float(lat_arr[0]),
        lon_min=float(lon_arr[0]),
        step_deg=GRID_STEP_DEG,
        valid_times=[ref_dt + timedelta(hours=h) for h in horizons],
        solar=solar_cache,             solar_min=solar_min_cache,             solar_max=solar_max_cache,
        sunshine=sunshine_cache,       sunshine_min=sunshine_min_cache,       sunshine_max=sunshine_max_cache,
        cloud_cover=cloud_cover_cache, cloud_cover_min=cloud_cover_min_cache, cloud_cover_max=cloud_cover_max_cache,
        cloud_low=cloud_low_cache,     cloud_low_min=cloud_low_min_cache,     cloud_low_max=cloud_low_max_cache,
        cloud_mid=cloud_mid_cache,     cloud_mid_min=cloud_mid_min_cache,     cloud_mid_max=cloud_mid_max_cache,
        cloud_high=cloud_high_cache,   cloud_high_min=cloud_high_min_cache,   cloud_high_max=cloud_high_max_cache,
        freezing_level=freezing_level_cache, freezing_level_min=freezing_level_min_cache, freezing_level_max=freezing_level_max_cache,
        cape=cape_cache,               cape_min=cape_min_cache,               cape_max=cape_max_cache,
        cin=cin_cache,                 cin_min=cin_min_cache,                 cin_max=cin_max_cache,
        lcl=lcl_cache,                 lcl_min=lcl_min_cache,                 lcl_max=lcl_max_cache,
        lfc=lfc_cache,                 lfc_min=lfc_min_cache,                 lfc_max=lfc_max_cache,
        tke=tke_cache,                 tke_min=tke_min_cache,                 tke_max=tke_max_cache,
    )


def _build_grid_wind_cache(
    horizons: list[int],
    ref_dt: datetime,
    tmpdir: Path,
    level_indices: dict[int, int],
    sample_indices: np.ndarray,
    n_lat: int,
    n_lon: int,
    model: str,
) -> GridWindCache:
    """Build a GridWindCache from U/V/T_2M/TD_2M GRIBs kept on disk in tmpdir.

    Reads one horizon at a time and deletes each file immediately after extraction.
    Called by both IconCh1EpsCollector and IconCh2EpsCollector.
    """
    n_grid = len(sample_indices)
    n_horizons = len(horizons)
    alt_m_order = sorted(ALTITUDE_TO_HPA.keys())

    # float16 storage — ws (km/h), wd (deg), rh (%) all fit float16 with far finer
    # resolution than the data warrants; halves resident grid memory. Stats are computed
    # in float64 below and cast to float16 only on store.
    ws_cache: dict[int, np.ndarray] = {
        alt_m: np.full((n_horizons, n_grid), np.nan, dtype=np.float16) for alt_m in alt_m_order
    }
    wd_cache: dict[int, np.ndarray] = {
        alt_m: np.full((n_horizons, n_grid), np.nan, dtype=np.float16) for alt_m in alt_m_order
    }
    rh_cache = np.full((n_horizons, n_grid), np.nan, dtype=np.float16)

    for h_idx, h in enumerate(horizons):
        u_grid: np.ndarray | None = None
        v_grid: np.ndarray | None = None

        for var in ("U", "V"):
            dest = tmpdir / f"{var}_{h:03d}.grib2"
            if not dest.exists():
                continue
            try:
                arr, _ = _read_grib2_eccodes(dest, extract_indices=sample_indices)
                if arr is None or arr.ndim < 3:
                    continue
                if var == "U":
                    u_grid = arr
                else:
                    v_grid = arr
            except Exception as exc:
                logger.warning("Grid parse %s h=%d: %s", var, h, exc)
            finally:
                dest.unlink(missing_ok=True)

        if u_grid is not None and v_grid is not None:
            for alt_m in alt_m_order:
                l_idx = level_indices.get(ALTITUDE_TO_HPA[alt_m])
                if l_idx is None:
                    continue
                u = u_grid[:, l_idx, :].astype(np.float64)
                v = v_grid[:, l_idx, :].astype(np.float64)
                speeds = np.sqrt(u ** 2 + v ** 2) * 3.6
                dirs = (270.0 - np.degrees(np.arctan2(v, u))) % 360.0
                rad = np.deg2rad(dirs)
                ws_cache[alt_m][h_idx] = np.nanmedian(speeds, axis=0).astype(np.float16)
                wd_cache[alt_m][h_idx] = (np.degrees(np.arctan2(
                    np.nanmedian(np.sin(rad), axis=0),
                    np.nanmedian(np.cos(rad), axis=0),
                )) % 360.0).astype(np.float16)

        t_dest  = tmpdir / f"T_2M_{h:03d}.grib2"
        td_dest = tmpdir / f"TD_2M_{h:03d}.grib2"
        t_arr: np.ndarray | None = None
        td_arr: np.ndarray | None = None
        for dest, store in ((t_dest, "t"), (td_dest, "td")):
            if not dest.exists():
                continue
            try:
                arr, _ = _read_grib2_eccodes(dest, extract_indices=sample_indices)
                if arr is not None and arr.ndim == 2:
                    if store == "t":
                        t_arr = arr
                    else:
                        td_arr = arr
            except Exception as exc:
                logger.warning("Grid parse %s h=%d: %s", dest.name, h, exc)
            finally:
                dest.unlink(missing_ok=True)

        if t_arr is not None and td_arr is not None:
            rh_members = _compute_rh_from_td(
                t_arr.astype(np.float64), td_arr.astype(np.float64),
            )
            rh_cache[h_idx] = np.nanmedian(rh_members, axis=0).astype(np.float16)

        logger.debug("Grid %s h=%d computed", model, h)

    lat_arr = np.arange(GRID_LAT_MAX, GRID_LAT_MIN - GRID_STEP_DEG / 2, -GRID_STEP_DEG)
    lon_arr = np.arange(GRID_LON_MIN, GRID_LON_MAX + GRID_STEP_DEG / 2, GRID_STEP_DEG)
    lon_grid_2d, lat_grid_2d = np.meshgrid(lon_arr, lat_arr)

    return GridWindCache(
        model=model,
        init_time=ref_dt,
        lats=lat_grid_2d.ravel().astype(np.float32),
        lons=lon_grid_2d.ravel().astype(np.float32),
        n_lat=n_lat,
        n_lon=n_lon,
        lat_max=float(lat_arr[0]),
        lon_min=float(lon_arr[0]),
        step_deg=GRID_STEP_DEG,
        valid_times=[ref_dt + timedelta(hours=h) for h in horizons],
        ws=ws_cache,
        wd=wd_cache,
        rh=rh_cache,
    )


# ---------- Collector ----------

class IconCh1EpsCollector(BaseCollector):
    """ICON-CH1-EPS collector — 0–30 h, 4 runs/day (00Z/06Z/12Z/18Z), 11 members."""

    async def _ensure_grid(self, tmpdir: Path) -> None:
        global _GRID_TREE, _GRID_LATS, _GRID_LONS
        global _GRID_SAMPLE_INDICES, _GRID_N_LAT, _GRID_N_LON

        if _GRID_TREE is not None:
            return

        cfg = get_config()
        logger.info("Fetching collection metadata for grid constants")
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(
                f"{cfg.meteoswiss.stac_base_url}/collections/{COLLECTION}"
            )
            resp.raise_for_status()
            collection_meta = resp.json()

        constants_url: str | None = None
        for key, asset in collection_meta.get("assets", {}).items():
            if "horizontal_constants" in key.lower():
                constants_url = asset.get("href")
                break

        if not constants_url:
            raise RuntimeError(
                f"horizontal_constants asset not found in collection {COLLECTION}"
            )

        dest = tmpdir / "horizontal_constants_ch1.grib2"
        await self.download(constants_url, str(dest))

        lats, lons = _read_grid_coords(dest)
        _GRID_LATS = lats
        _GRID_LONS = lons

        flat_coords = np.column_stack([lats, lons])
        _GRID_TREE = cKDTree(flat_coords)
        logger.info("KD-tree built: %d grid points", len(lats))

        # Pre-sample regular 1 km grid over the default Switzerland bbox
        lat_arr = np.arange(GRID_LAT_MAX, GRID_LAT_MIN - GRID_STEP_DEG / 2, -GRID_STEP_DEG)
        lon_arr = np.arange(GRID_LON_MIN, GRID_LON_MAX + GRID_STEP_DEG / 2, GRID_STEP_DEG)
        _GRID_N_LAT = len(lat_arr)
        _GRID_N_LON = len(lon_arr)

        lon_grid, lat_grid = np.meshgrid(lon_arr, lat_arr)
        sample_coords = np.column_stack([lat_grid.ravel(), lon_grid.ravel()])
        _, _GRID_SAMPLE_INDICES = _GRID_TREE.query(sample_coords)
        logger.info(
            "Grid sample indices computed: %d × %d = %d points",
            _GRID_N_LAT, _GRID_N_LON, len(_GRID_SAMPLE_INDICES),
        )

    async def _fetch_stations(self) -> list[dict]:
        cfg = get_config()
        async with httpx.AsyncClient(timeout=30, verify=False) as client:
            resp = await client.get(f"{cfg.lenticularis.base_url}/api/stations")
            resp.raise_for_status()
            return resp.json()

    async def _fetch_step(
        self,
        semaphore: asyncio.Semaphore,
        client: httpx.AsyncClient,
        ref_dt: datetime,
        variable: str,
        horizon_h: int,
        station_flat_indices: np.ndarray,
        tmpdir: Path,
        keep: bool = False,
    ) -> np.ndarray | None:
        """Download one (variable, horizon) GRIB, extract station values, return array.

        keep=True skips deletion so the file can be re-read for grid extraction.
        """
        cfg = get_config()
        async with semaphore:
            try:
                url = await _search_item_url(
                    client, cfg.meteoswiss.stac_base_url, COLLECTION,
                    ref_dt, variable, horizon_h,
                )
            except Exception as exc:
                logger.error("STAC search error %s h=%d: %s", variable, horizon_h, exc)
                _telemetry.record_download_error("ch1", variable, horizon_h, f"STAC search: {exc}")
                return None

            if url is None:
                logger.warning("CH1 STAC: no asset found for %s h=%d — variable may not be published", variable, horizon_h)
                return None

            dest = tmpdir / f"{variable}_{horizon_h:03d}.grib2"
            if dest.exists() and dest.stat().st_size > 1024:
                logger.debug("CH1 GRIB cache hit: %s h=%d", variable, horizon_h)
            else:
                try:
                    await self.download(url, str(dest))
                except Exception as exc:
                    logger.error("Download failed %s h=%d: %s", variable, horizon_h, exc)
                    _telemetry.record_download_error("ch1", variable, horizon_h, f"Download: {exc}")
                    return None

        try:
            arr, _level_coords = _read_grib2_eccodes(dest, extract_indices=station_flat_indices)
            if arr is None:
                logger.warning("eccodes returned None for %s h=%d", variable, horizon_h)
                return None
            logger.debug("CH1 data %s h=%d shape=%s", variable, horizon_h, arr.shape)
            return arr
        except Exception as exc:
            logger.error("eccodes read failed %s h=%d: %s — removing cached file", variable, horizon_h, exc)
            dest.unlink(missing_ok=True)
            _telemetry.record_download_error("ch1", variable, horizon_h, f"eccodes: {exc}")
            return None

    def collect_grid(
        self,
        ref_dt: datetime,
        tmpdir: Path,
        level_indices: dict[int, int],
    ) -> None:
        if _GRID_SAMPLE_INDICES is None:
            logger.info("Grid sample indices not available; skipping CH1 grid collection")
            return
        cache = _build_grid_wind_cache(
            HORIZONS, ref_dt, tmpdir, level_indices,
            _GRID_SAMPLE_INDICES, _GRID_N_LAT, _GRID_N_LON, "icon-ch1",
        )
        set_grid_wind_cache(cache)
        logger.info(
            "CH1 GridWindCache set: %d × %d points, %d levels, %d frames, init_time=%s",
            _GRID_N_LAT, _GRID_N_LON, len(ALTITUDE_TO_HPA), len(HORIZONS), ref_dt.isoformat(),
        )
        thermal = _build_thermal_grid_cache(
            HORIZONS, ref_dt, tmpdir,
            _GRID_SAMPLE_INDICES, _GRID_N_LAT, _GRID_N_LON, "icon-ch1",
            accum_prior_h=None,
        )
        set_thermal_grid_cache(thermal)
        logger.info(
            "CH1 ThermalGridCache set: %d × %d points, %d frames, init_time=%s",
            _GRID_N_LAT, _GRID_N_LON, len(HORIZONS), ref_dt.isoformat(),
        )

    async def collect(self) -> None:  # noqa: C901
        ref_dt = _latest_ref_dt()
        logger.info("IconCh1EpsCollector.collect() ref_dt=%s", ref_dt.isoformat())

        with grib_run_dir("ch1", ref_dt) as tmpdir:

            await self._ensure_grid(tmpdir)
            stations = await self._fetch_stations()
            if not stations:
                logger.warning("No stations returned; skipping collection")
                return

            n_stations = len(stations)
            station_lats = np.array([s["latitude"] for s in stations])
            station_lons = np.array([s["longitude"] for s in stations])
            _, station_flat_indices = _GRID_TREE.query(
                np.column_stack([station_lats, station_lons])
            )

            semaphore = asyncio.Semaphore(DOWNLOAD_CONCURRENCY)
            cfg = get_config()

            async with httpx.AsyncClient(timeout=300) as client:

                level_hpa: np.ndarray | None = None
                u0_url = await _search_item_url(
                    client, cfg.meteoswiss.stac_base_url, COLLECTION, ref_dt, "U", HORIZONS[0]
                )
                if u0_url:
                    dest = tmpdir / "U_probe.grib2"
                    try:
                        await self.download(u0_url, str(dest))
                        _, level_hpa = _read_grib2_eccodes(dest, extract_indices=np.array([0]))
                    except Exception as exc:
                        logger.warning("Pressure level probe failed: %s", exc)
                    finally:
                        dest.unlink(missing_ok=True)

                if level_hpa is None:
                    logger.info("No pressure-level data for %s — skipping U/V/W", COLLECTION)
                    level_hpa = np.array(sorted(ALTITUDE_TO_HPA.values(), reverse=True), dtype=float)

                has_pressure_levels = u0_url is not None

                n_surf = len(SURFACE_VARS) * len(HORIZONS)
                n_pres = len(CH1_PRESSURE_VARS) * len(HORIZONS) if has_pressure_levels else 0
                progress = [0, 0, n_surf + n_pres]  # [done, ok, total]
                _cs.mark_running("ch1", ref_dt, n_surf + n_pres)

                async def fetch(var: str, h: int) -> np.ndarray | None:
                    result: np.ndarray | None = None
                    try:
                        result = await self._fetch_step(
                            semaphore, client, ref_dt, var, h, station_flat_indices, tmpdir,
                        )
                        return result
                    finally:
                        progress[0] += 1
                        if result is not None:
                            progress[1] += 1
                        done, ok, total = progress
                        _cs.mark_progress("ch1", done, ok)
                        if done % 20 == 0 or done == total:
                            logger.info("CH1 %d/%d ok=%d (%s h=%d)", done, total, ok, var, h)

                surf_tasks: dict[str, list[asyncio.Task]] = {v: [] for v in SURFACE_VARS}
                for var in SURFACE_VARS:
                    for h in HORIZONS:
                        surf_tasks[var].append(asyncio.ensure_future(fetch(var, h)))

                pres_tasks: dict[str, list[asyncio.Task]] = {v: [] for v in CH1_PRESSURE_VARS}
                if has_pressure_levels:
                    for var in CH1_PRESSURE_VARS:
                        for h in HORIZONS:
                            pres_tasks[var].append(asyncio.ensure_future(fetch(var, h)))

                all_tasks = [t for ts in surf_tasks.values() for t in ts]
                all_tasks += [t for ts in pres_tasks.values() for t in ts]
                await asyncio.gather(*all_tasks, return_exceptions=True)

            def _task_ok(t: asyncio.Task) -> bool:
                if t.cancelled():
                    return False
                try:
                    return isinstance(t.result(), np.ndarray)
                except Exception:
                    return False

            n_surf_ok = sum(1 for ts in surf_tasks.values() for t in ts if _task_ok(t))
            n_surf_total = sum(len(ts) for ts in surf_tasks.values())
            logger.info("CH1 surface fetch: %d/%d tasks returned data", n_surf_ok, n_surf_total)

            # Member count is a property of the model run — read it from the data.
            # Never compare against a hardcoded constant: MeteoSwiss may change the
            # ensemble size and the code should silently adapt.
            _n_members: int = next(
                (
                    t.result().shape[0]
                    for ts in surf_tasks.values()
                    for t in ts
                    if _task_ok(t) and t.result().ndim == 2
                ),
                1,
            )
            logger.info("CH1 ensemble members in GRIB: %d", _n_members)

            _nan_surf = np.full((_n_members, n_stations), np.nan)

            def surf_array(var: str) -> np.ndarray:
                steps = []
                for task in surf_tasks[var]:
                    r = task.result() if not task.cancelled() else None
                    if isinstance(r, np.ndarray) and r.ndim == 3:
                        r = r[:, -1, :]
                    steps.append(
                        r if isinstance(r, np.ndarray) and r.shape == _nan_surf.shape
                        else _nan_surf
                    )
                return np.stack(steps, axis=0)

            def pres_array(var: str) -> np.ndarray:
                if not pres_tasks.get(var):
                    return np.full((len(HORIZONS), _n_members, len(level_hpa), n_stations), np.nan)
                nan_pres = np.full((_n_members, len(level_hpa), n_stations), np.nan)
                steps = []
                for task in pres_tasks[var]:
                    r = task.result() if not task.cancelled() else None
                    steps.append(
                        r if isinstance(r, np.ndarray) and r.shape == nan_pres.shape
                        else nan_pres
                    )
                return np.stack(steps, axis=0)

            u_10m = surf_array("U_10M");   v_10m = surf_array("V_10M")
            vmax_10m = surf_array("VMAX_10M")
            t_2m = surf_array("T_2M");     td_2m = surf_array("TD_2M")
            pmsl = surf_array("PMSL")
            tot_prec = surf_array("TOT_PREC");   dursun = surf_array("DURSUN")
            aswdir_s = surf_array("ASWDIR_S");   aswdifd_s = surf_array("ASWDIFD_S")
            clct = surf_array("CLCT");  clcl = surf_array("CLCL")
            clcm = surf_array("CLCM");  clch = surf_array("CLCH")
            hzerocl = surf_array("HZEROCL");     cape_ml = surf_array("CAPE_ML")
            cin_ml = surf_array("CIN_ML")
            u_pl = pres_array("U");  v_pl = pres_array("V");  w_pl = pres_array("W")

            def deaccum(arr: np.ndarray) -> np.ndarray:
                out = np.empty_like(arr)
                for s in range(n_stations):
                    out[:, :, s] = _deaccumulate(arr[:, :, s])
                return out

            prec_rate    = np.clip(deaccum(tot_prec), 0.0, None)
            dursun_min   = np.clip(deaccum(dursun) / 60.0, 0.0, None)
            solar_direct = np.clip(deaccum(aswdir_s) / 3600.0, 0.0, None)
            solar_diffuse = np.clip(deaccum(aswdifd_s) / 3600.0, 0.0, None)
            rh       = _compute_rh_from_td(t_2m, td_2m)
            t_c      = t_2m - 273.15
            pmsl_hpa = pmsl / 100.0

            level_indices: dict[int, int] = _build_level_indices(level_hpa)
            logger.info(
                "CH1 level_indices (target_hpa→arr_idx): %s",
                {k: v for k, v in sorted(level_indices.items())},
            )
            alt_m_order = sorted(ALTITUDE_TO_HPA.keys())

            for s_idx, station in enumerate(stations):
                station_id = station["station_id"]
                lat  = float(station["latitude"])
                lon  = float(station["longitude"])
                elev = int(station["elevation"]) if station.get("elevation") is not None else 0

                forecast_list: list[StationForecastHour] = []
                profiles_list: list[AltitudeWindsProfile] = []

                for h_idx, h in enumerate(HORIZONS):
                    valid_time = ref_dt + timedelta(hours=h)

                    def s(arr: np.ndarray) -> EnsembleValue:
                        return _to_ensemble_value(arr[h_idx, :, s_idx])

                    ws_ev, wd_ev = _wind_ensemble_value(
                        u_10m[h_idx, :, s_idx], v_10m[h_idx, :, s_idx]
                    )
                    wg_ev = s(vmax_10m)
                    t_ev  = s(t_c)
                    rh_ev = s(rh)
                    p_ev  = s(pmsl_hpa)
                    pr_ev = s(prec_rate)

                    ws_p, ws_mn, ws_mx = _ev_flat(ws_ev, scale=3.6)
                    wg_p, wg_mn, wg_mx = _ev_flat(wg_ev, scale=3.6)
                    wd_p, wd_mn, wd_mx = _ev_flat(wd_ev)
                    t_p,  t_mn,  t_mx  = _ev_flat(t_ev)
                    rh_p, rh_mn, rh_mx = _ev_flat(rh_ev)
                    p_p,  p_mn,  p_mx  = _ev_flat(p_ev)
                    pr_p, pr_mn, pr_mx = _ev_flat(pr_ev)

                    forecast_list.append(StationForecastHour(
                        valid_time=valid_time,
                        wind_speed=ws_p, wind_speed_min=ws_mn, wind_speed_max=ws_mx,
                        wind_gust=wg_p, wind_gust_min=wg_mn, wind_gust_max=wg_mx,
                        wind_direction=wd_p, wind_direction_min=wd_mn, wind_direction_max=wd_mx,
                        temperature=t_p, temperature_min=t_mn, temperature_max=t_mx,
                        humidity=rh_p, humidity_min=rh_mn, humidity_max=rh_mx,
                        pressure_qff=p_p, pressure_qff_min=p_mn, pressure_qff_max=p_mx,
                        precipitation=pr_p, precipitation_min=pr_mn, precipitation_max=pr_mx,
                    ))

                    level_list: list[AltitudeWindLevel] = []
                    for alt_m in alt_m_order:
                        l_idx = level_indices.get(ALTITUDE_TO_HPA[alt_m])
                        if l_idx is not None:
                            pl_ws_ev, pl_wd_ev = _wind_ensemble_value(
                                u_pl[h_idx, :, l_idx, s_idx], v_pl[h_idx, :, l_idx, s_idx]
                            )
                            pl_wv_ev = _to_ensemble_value(w_pl[h_idx, :, l_idx, s_idx])
                        else:
                            nan_ev = EnsembleValue(probable=None, min=None, max=None)
                            pl_ws_ev = pl_wd_ev = pl_wv_ev = nan_ev

                        pl_ws_p, pl_ws_mn, pl_ws_mx = _ev_flat(pl_ws_ev, scale=3.6)
                        pl_wd_p, pl_wd_mn, pl_wd_mx = _ev_flat(pl_wd_ev)
                        pl_wv_p, pl_wv_mn, pl_wv_mx = _ev_flat(pl_wv_ev)

                        level_list.append(AltitudeWindLevel(
                            level_m=alt_m,
                            wind_speed=pl_ws_p, wind_speed_min=pl_ws_mn, wind_speed_max=pl_ws_mx,
                            wind_direction=pl_wd_p, wind_direction_min=pl_wd_mn, wind_direction_max=pl_wd_mx,
                            vertical_wind=pl_wv_p, vertical_wind_min=pl_wv_mn, vertical_wind_max=pl_wv_mx,
                        ))
                    profiles_list.append(AltitudeWindsProfile(valid_time=valid_time, levels=level_list))

                set_station_forecast(
                    station_id,
                    StationForecastResponse(
                        station_id=station_id,
                        init_time=ref_dt,
                        model="icon-ch1",
                        source="swissmeteo",
                        forecast=forecast_list,
                    ),
                )
                set_station_altitude_winds(
                    station_id,
                    AltitudeWindsResponse(
                        station_id=station_id,
                        init_time=ref_dt,
                        model="icon-ch1",
                        source="swissmeteo",
                        profiles=profiles_list,
                    ),
                )
                logger.info("Cached forecast for %s (%d hours)", station_id, len(forecast_list))

            # Grid collection reads the U/V GRIBs kept in tmpdir. Off the event loop:
            # it parses hundreds of GRIBs (~8 min for CH1) and would otherwise block
            # uvicorn for that whole window — /health included. eccodes (via cffi) and
            # numpy release the GIL, so the loop keeps serving while this runs.
            try:
                await asyncio.to_thread(self.collect_grid, ref_dt, tmpdir, level_indices)
            except Exception:
                logger.exception("Grid collection failed — station data unaffected")

        logger.info("IconCh1EpsCollector.collect() complete — %d stations", len(stations))
