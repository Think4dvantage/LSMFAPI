import asyncio
import logging
import math
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

# Pre-sampled 1 km regular grid indices into _GRID_TREE for the default bbox
_GRID_SAMPLE_INDICES: np.ndarray | None = None
_GRID_N_LAT: int = 0
_GRID_N_LON: int = 0

# Full-level geometric heights (m MSL) per ICON grid point, derived once from the static
# HHL vertical constants. Shape (n_full_levels, n_grid_points), float16 (~184 MB for CH1's
# 1.14 M points; heights round to ~4 m at 4 km — ample for wind interpolation). Sliced at
# station / grid-sample indices to interpolate altitude winds onto fixed MAMSL bands.
_GRID_LEVEL_HEIGHTS: np.ndarray | None = None

# Default Switzerland bbox for grid pre-sampling
GRID_LAT_MAX = 47.9
GRID_LAT_MIN = 45.8
GRID_LON_MIN = 5.9
GRID_LON_MAX = 10.6
GRID_STEP_DEG = 1.0 / 111.0  # ~1 km

# One-time vertical-structure diagnostic guard, keyed by typeOfLevel (see
# _read_grib2_eccodes). Lets a multi-level GRIB log its level layout exactly once
# per process instead of once per file.
_VLEVEL_DIAG_SEEN: set[str] = set()

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
# Altitude bands reported by the altitude-winds endpoint and the wind grid, in metres
# above mean sea level. Winds are interpolated to these exact geometric heights per grid
# point from the model levels (see _interp_to_heights); bands below a point's terrain
# resolve to null. Replaces the old pressure-level mapping — the EPS U/V/W files carry no
# pressure coordinate (generalVerticalLayer model levels, no pv), so heights come from HHL.
ALTITUDE_TARGETS_M: list[int] = [500, 800, 1000, 1500, 2000, 2500, 3000, 4000, 5000]

DOWNLOAD_CONCURRENCY = 6


# ---------- Pure helpers ----------

def _horizon_str(h: int) -> str:
    return f"P0DT{h:02d}H00M00S"


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
    # Closed interval (start == end): a run takes 1.5-2h and MeteoSwiss publishes
    # continuously, so an open-ended "ref_dt/.." interval can match a newer run that
    # appeared mid-collection, silently stitching two different model runs together.
    ref_dt_str = ref_dt.strftime('%Y-%m-%dT%H:%M:%SZ')
    payload = {
        "collections": [collection],
        "forecast:reference_datetime": f"{ref_dt_str}/{ref_dt_str}",
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
    if len(assets) > 1:
        logger.warning(
            "STAC search %s h=%d ref=%s returned %d assets, expected 1 — using the first",
            variable, horizon_h, ref_dt_str, len(assets),
        )
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
    # zeros_like, not arr[:1, :] * 0 — a NaN in the first step (a failed fetch, see
    # _nan_surf) makes "anything * 0" itself NaN, silently breaking the zero-baseline
    # intent for that column.
    return np.diff(arr, axis=0, prepend=np.zeros_like(arr[:1, :]))


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


def _interp_to_heights(
    field: np.ndarray,
    level_heights: np.ndarray,
    targets_m: np.ndarray,
) -> np.ndarray:
    """Linearly interpolate a per-model-level field onto fixed MAMSL target heights.

    field         : (M, L, N) — members × model levels × grid points
    level_heights : (L, N)     — geometric height (m MSL) of each model level per point
    targets_m     : (T,)       — target heights (m MSL)
    returns       : (M, T, N)  — NaN where a target is below the point's lowest model level
                                 (underground) or above its highest.

    ICON model levels run top→bottom (height decreasing with index); the level axis is
    flipped once to ascending height for a vectorised per-column bracket search. Heights
    are static, so the same level_heights serve every horizon. Kept in float32 to avoid a
    large float64 copy of the (members × levels × points) input.
    """
    if level_heights.shape[0] != field.shape[1]:
        # Level count mismatch (unexpected) — cannot align; return all-NaN rather than guess.
        return np.full((field.shape[0], len(targets_m), field.shape[2]), np.nan, dtype=np.float32)

    z = level_heights.astype(np.float32)
    f = field if field.dtype == np.float32 else field.astype(np.float32)
    if z[0, 0] > z[-1, 0]:            # top→bottom → flip to ascending height
        z = z[::-1]
        f = f[:, ::-1, :]

    M, L, N = f.shape
    col = np.arange(N)
    out = np.full((M, len(targets_m), N), np.nan, dtype=np.float32)
    with np.errstate(invalid="ignore", divide="ignore"):
        for ti in range(len(targets_m)):
            t = float(targets_m[ti])
            lower = (z <= t).sum(axis=0) - 1      # (N,) lower bracket index; -1 => below lowest
            valid = (lower >= 0) & (lower < L - 1)
            k = np.clip(lower, 0, L - 2)
            z0 = z[k, col]
            z1 = z[k + 1, col]
            denom = z1 - z0
            w = np.where(denom > 0, (t - z0) / np.where(denom > 0, denom, 1.0), np.float32(0.0))
            vals = f[:, k, col] * (1.0 - w) + f[:, k + 1, col] * w   # (M, N)
            out[:, ti, :] = np.where(valid, vals, np.nan)
    return out


def _load_level_heights(path: Path) -> np.ndarray:
    """Read HHL (height of half-levels) from a vertical_constants GRIB and return the
    full-level geometric heights (m MSL), shape (n_full = n_half - 1, n_points), float16.

    Full-level height is the mean of the two bounding half-levels. Computed level-by-level
    to avoid a second full-size float32 temporary (the raw HHL array is already ~370 MB for
    CH1's 1.14 M points)."""
    hhl, _ = _read_grib2_eccodes(path)           # (1, n_half, n_points), float32
    if hhl is None or hhl.ndim != 3:
        raise RuntimeError(f"HHL not found or unexpected shape in {path.name}")
    half = hhl[0]                                 # (n_half, N) view
    n_full = half.shape[0] - 1
    full = np.empty((n_full, half.shape[1]), dtype=np.float16)
    for k in range(n_full):
        full[k] = 0.5 * (half[k] + half[k + 1])
    return full


def _read_grib2_eccodes(
    path: Path,
    extract_indices: np.ndarray | None = None,
) -> tuple[np.ndarray | None, np.ndarray | None]:
    """Read a forecast GRIB2 file with eccodes, returning (values, level_coords).

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
    level_coords : None for surface; for multi-level files the sorted model-level
                 numbers (generalVerticalLayer). These carry no pressure — the EPS
                 U/V/W files have no pv — so geometric height comes from the static
                 HHL constants (see _load_level_heights / _interp_to_heights).
    """
    # Pass 1: metadata scan — no values arrays stored
    unique_members: set[int] = set()
    unique_levels: set[int] = set()
    n_points: int = 0
    _level_type: str = ""
    _pert_fallback_count = 0  # messages where perturbationNumber couldn't be read

    try:
        with open(str(path), "rb") as f:
            while True:
                msg = eccodes.codes_grib_new_from_file(f)
                if msg is None:
                    break
                try:
                    pert = _eccodes_get(msg, "perturbationNumber", default=None)
                    if pert is None:
                        _pert_fallback_count += 1
                        pert = 0
                    unique_members.add(int(pert))
                    unique_levels.add(int(_eccodes_get(msg, "level", default=0)))
                    if n_points == 0:
                        n_points = eccodes.codes_get_size(msg, "values")
                    if not _level_type:
                        _level_type = _eccodes_get(msg, "typeOfLevel", default="") or ""
                finally:
                    eccodes.codes_release(msg)
    except Exception as exc:
        logger.error("eccodes read failed for %s: %s", path.name, exc)
        raise

    if not unique_members or n_points == 0:
        logger.warning("No GRIB2 messages in %s", path.name)
        return None, None

    # Exactly one message defaulting to member 0 is the expected control run. More than
    # one means perturbationNumber genuinely failed to read on real ensemble members,
    # which would otherwise silently collapse them all onto the same output row.
    if _pert_fallback_count > 1:
        logger.warning(
            "%s: perturbationNumber missing on %d messages (expected at most 1, for the "
            "control run) — ensemble members may have collapsed",
            path.name, _pert_fallback_count,
        )

    sorted_members = sorted(unique_members)
    sorted_levels  = sorted(unique_levels)
    is_surface = len(sorted_levels) == 1

    member_idx = {m: i for i, m in enumerate(sorted_members)}
    level_idx  = {lvl: i for i, lvl in enumerate(sorted_levels)}

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
        # level_coords are the raw model-level numbers. EPS U/V/W arrive as ~80
        # generalVerticalLayer levels with no embedded pressure (no pv), so pressure is not
        # recoverable here; geometric height comes from the static HHL constants instead
        # (see _load_level_heights / _interp_to_heights). Callers use level_coords only for
        # the level count, not for physical values.
        level_coords = np.array(sorted_levels, dtype=float)
        if _level_type not in _VLEVEL_DIAG_SEEN:
            _VLEVEL_DIAG_SEEN.add(_level_type)
            logger.info(
                "GRIB multi-level structure [%s]: typeOfLevel=%r n_levels=%d level_range=[%d, %d]",
                path.name, _level_type, len(sorted_levels), sorted_levels[0], sorted_levels[-1],
            )
        return arr, level_coords

    return arr, None


# ---------- Shared grid helper (used by CH1 and CH2 collectors) ----------

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

    solar_cache        = _nan()
    solar_min_cache        = _nan()
    solar_max_cache        = _nan()
    sunshine_cache     = _nan()
    sunshine_min_cache     = _nan()
    sunshine_max_cache     = _nan()
    cloud_cover_cache  = _nan()
    cloud_cover_min_cache  = _nan()
    cloud_cover_max_cache  = _nan()
    cloud_low_cache    = _nan()
    cloud_low_min_cache    = _nan()
    cloud_low_max_cache    = _nan()
    cloud_mid_cache    = _nan()
    cloud_mid_min_cache    = _nan()
    cloud_mid_max_cache    = _nan()
    cloud_high_cache   = _nan()
    cloud_high_min_cache   = _nan()
    cloud_high_max_cache   = _nan()
    freezing_level_cache = _nan()
    freezing_level_min_cache = _nan()
    freezing_level_max_cache = _nan()
    cape_cache         = _nan()
    cape_min_cache         = _nan()
    cape_max_cache         = _nan()
    cin_cache          = _nan()
    cin_min_cache          = _nan()
    cin_max_cache          = _nan()
    lcl_cache          = _nan()
    lcl_min_cache          = _nan()
    lcl_max_cache          = _nan()
    lfc_cache          = _nan()
    lfc_min_cache          = _nan()
    lfc_max_cache          = _nan()
    tke_cache          = _nan()
    tke_min_cache          = _nan()
    tke_max_cache          = _nan()

    def _read(var: str, h: int) -> np.ndarray | None:
        dest = tmpdir / f"{var}_{h:03d}.grib2"
        if not dest.exists():
            logger.warning("ThermalGrid %s h=%d: file missing, treating as no data", var, h)
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

    # All-NaN columns are expected here (e.g. CIN's ICON fill value clipped to NaN for every
    # member at a point) — errstate suppresses the resulting RuntimeWarning noise without
    # masking genuine warnings elsewhere, matching _interp_to_heights's existing pattern.
    def _median(arr: np.ndarray) -> np.ndarray:
        with np.errstate(invalid="ignore"):
            return np.nanmedian(arr, axis=0).astype(np.float16)

    def _nanmin(arr: np.ndarray) -> np.ndarray:
        with np.errstate(invalid="ignore"):
            return np.nanmin(arr, axis=0).astype(np.float16)

    def _nanmax(arr: np.ndarray) -> np.ndarray:
        with np.errstate(invalid="ignore"):
            return np.nanmax(arr, axis=0).astype(np.float16)

    # Load prior accumulated values for deaccumulation baseline
    prev_aswdir: np.ndarray | None = None
    prev_aswdifd: np.ndarray | None = None
    prev_dursun: np.ndarray | None = None
    if accum_prior_h is not None:
        prev_aswdir  = _read("ASWDIR_S",  accum_prior_h)
        prev_aswdifd = _read("ASWDIFD_S", accum_prior_h)
        prev_dursun  = _read("DURSUN",    accum_prior_h)

    # True once a real baseline exists: either the accum_prior_h fetch above succeeded, or
    # there's no accum_prior_h and we're at true model start, where baseline 0 is correct.
    # A missing horizon must flip this back to False — otherwise the next successful step
    # would silently difference against a stale (2-hour-old) baseline, reading ~2x too high
    # instead of surfacing as the null the station path already yields in this situation.
    aswdir_baseline_ok = accum_prior_h is None or prev_aswdir is not None
    aswdifd_baseline_ok = accum_prior_h is None or prev_aswdifd is not None
    dursun_baseline_ok = accum_prior_h is None or prev_dursun is not None

    for h_idx, h in enumerate(horizons):
        # --- accumulated: solar radiation ---
        raw_dir  = _read("ASWDIR_S",  h)
        raw_difd = _read("ASWDIFD_S", h)
        if raw_dir is not None and raw_difd is not None and aswdir_baseline_ok and aswdifd_baseline_ok:
            delta_dir  = raw_dir  - (prev_aswdir  if prev_aswdir  is not None else 0.0)
            delta_difd = raw_difd - (prev_aswdifd if prev_aswdifd is not None else 0.0)
            solar_w_m2 = np.clip(delta_dir + delta_difd, 0.0, None) / 3600.0  # J/m² → W/m²
            solar_cache[h_idx]     = _median(solar_w_m2)
            solar_min_cache[h_idx] = _nanmin(solar_w_m2)
            solar_max_cache[h_idx] = _nanmax(solar_w_m2)
            prev_aswdir  = raw_dir
            prev_aswdifd = raw_difd
            aswdir_baseline_ok = aswdifd_baseline_ok = True
        else:
            if raw_dir is None or raw_difd is None:
                logger.warning(
                    "%s thermal-grid solar baseline gap at h=%d — this and the next step's "
                    "delta are null rather than risk a doubled value", model, h,
                )
            aswdir_baseline_ok = aswdifd_baseline_ok = False

        # --- accumulated: sunshine ---
        raw_dursun = _read("DURSUN", h)
        if raw_dursun is not None and dursun_baseline_ok:
            delta_dursun = raw_dursun - (prev_dursun if prev_dursun is not None else 0.0)
            sunshine_min = np.clip(delta_dursun, 0.0, None) / 60.0  # s → min
            sunshine_cache[h_idx]     = _median(sunshine_min)
            sunshine_min_cache[h_idx] = _nanmin(sunshine_min)
            sunshine_max_cache[h_idx] = _nanmax(sunshine_min)
            prev_dursun = raw_dursun
            dursun_baseline_ok = True
        else:
            if raw_dursun is None:
                logger.warning(
                    "%s thermal-grid sunshine baseline gap at h=%d — this and the next step's "
                    "delta are null rather than risk a doubled value", model, h,
                )
            dursun_baseline_ok = False

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
    level_heights: np.ndarray,
    sample_indices: np.ndarray,
    n_lat: int,
    n_lon: int,
    model: str,
) -> GridWindCache:
    """Build a GridWindCache from U/V/T_2M/TD_2M GRIBs kept on disk in tmpdir.

    level_heights is the model-level geometric height (m MSL) at each grid-sample point,
    shape (n_levels, n_grid); U/V are interpolated onto the fixed MAMSL bands per point.
    Reads one horizon at a time and deletes each file immediately after extraction.
    Called by both IconCh1EpsCollector and IconCh2EpsCollector.
    """
    n_grid = len(sample_indices)
    n_horizons = len(horizons)
    alt_m_order = ALTITUDE_TARGETS_M
    targets_m = np.array(ALTITUDE_TARGETS_M, dtype=np.float64)

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
            # Interpolate every member onto the MAMSL bands, then reduce across members.
            u_alt = _interp_to_heights(u_grid, level_heights, targets_m)   # (M, T, n_grid)
            v_alt = _interp_to_heights(v_grid, level_heights, targets_m)
            # Grid points below terrain at this band are legitimately all-NaN across every
            # member (see _interp_to_heights) — errstate suppresses the expected
            # RuntimeWarning without masking genuine warnings elsewhere.
            with np.errstate(invalid="ignore"):
                for ai, alt_m in enumerate(alt_m_order):
                    u = u_alt[:, ai, :].astype(np.float64)
                    v = v_alt[:, ai, :].astype(np.float64)
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
            with np.errstate(invalid="ignore"):
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
        global _GRID_TREE
        global _GRID_SAMPLE_INDICES, _GRID_N_LAT, _GRID_N_LON, _GRID_LEVEL_HEIGHTS

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

        assets = collection_meta.get("assets", {})
        constants_url: str | None = None
        vertical_url: str | None = None
        for key, asset in assets.items():
            if "horizontal_constants" in key.lower():
                constants_url = asset.get("href")
            elif "vertical_constants" in key.lower():
                vertical_url = asset.get("href")

        if not constants_url:
            raise RuntimeError(
                f"horizontal_constants asset not found in collection {COLLECTION}"
            )

        dest = tmpdir / "horizontal_constants_ch1.grib2"
        await self.download(constants_url, str(dest))

        lats, lons = _read_grid_coords(dest)

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

        # Model-level geometric heights (m MSL) from the static HHL vertical constants —
        # the basis for interpolating altitude winds / the wind grid to fixed MAMSL bands.
        if not vertical_url:
            raise RuntimeError(
                f"vertical_constants asset not found in collection {COLLECTION}"
            )
        vc_dest = tmpdir / "vertical_constants_ch1.grib2"
        await self.download(vertical_url, str(vc_dest))
        _GRID_LEVEL_HEIGHTS = _load_level_heights(vc_dest)
        vc_dest.unlink(missing_ok=True)
        logger.info(
            "CH1 model-level heights: %d levels × %d points, MAMSL range [%.0f, %.0f]",
            _GRID_LEVEL_HEIGHTS.shape[0], _GRID_LEVEL_HEIGHTS.shape[1],
            float(np.nanmin(_GRID_LEVEL_HEIGHTS)), float(np.nanmax(_GRID_LEVEL_HEIGHTS)),
        )

    async def _fetch_stations(self) -> list[dict]:
        cfg = get_config()
        async with httpx.AsyncClient(timeout=30) as client:
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
        delete_after_read: bool = False,
    ) -> np.ndarray | None:
        """Download one (variable, horizon) GRIB, extract station values, return array.

        delete_after_read=True for variables the grid build never reads (currently just
        "W", ~1.8 GB/file) — deletes right after this read instead of leaving it on disk
        until every horizon has downloaded, which is what actually drives the multi-hundred-
        GB peak (see P0-4 in specs/001-tech-debt-remediation/plan.md).
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
            arr, _level_coords = await asyncio.to_thread(
                _read_grib2_eccodes, dest, extract_indices=station_flat_indices,
            )
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
        finally:
            if delete_after_read:
                dest.unlink(missing_ok=True)

    def collect_grid(
        self,
        ref_dt: datetime,
        tmpdir: Path,
    ) -> None:
        if _GRID_SAMPLE_INDICES is None or _GRID_LEVEL_HEIGHTS is None:
            logger.info("Grid sample indices / level heights not available; skipping CH1 grid collection")
            return
        z_grid = _GRID_LEVEL_HEIGHTS[:, _GRID_SAMPLE_INDICES]
        cache = _build_grid_wind_cache(
            HORIZONS, ref_dt, tmpdir, z_grid,
            _GRID_SAMPLE_INDICES, _GRID_N_LAT, _GRID_N_LON, "icon-ch1",
        )
        set_grid_wind_cache(cache)
        logger.info(
            "CH1 GridWindCache set: %d × %d points, %d bands, %d frames, init_time=%s",
            _GRID_N_LAT, _GRID_N_LON, len(ALTITUDE_TARGETS_M), len(HORIZONS), ref_dt.isoformat(),
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

                pres_level_nums: np.ndarray | None = None
                u0_url = await _search_item_url(
                    client, cfg.meteoswiss.stac_base_url, COLLECTION, ref_dt, "U", HORIZONS[0]
                )
                if u0_url:
                    dest = tmpdir / "U_probe.grib2"
                    try:
                        await self.download(u0_url, str(dest))
                        # Second return is the model-level numbers — used only to size the
                        # pressure-var arrays; heights come from HHL (see _ensure_grid).
                        _, pres_level_nums = _read_grib2_eccodes(dest, extract_indices=np.array([0]))
                    except Exception as exc:
                        logger.warning("Pressure level probe failed: %s", exc)
                    finally:
                        dest.unlink(missing_ok=True)

                if pres_level_nums is None:
                    logger.info("Model-level probe empty for %s — assuming 80 levels", COLLECTION)
                    pres_level_nums = np.arange(1, 81, dtype=float)

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
                            delete_after_read=(var == "W"),
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
                # float32 so the stacked array matches the eccodes results and the height
                # interpolation needs no float64 copy of the (H·M × levels × stations) block.
                if not pres_tasks.get(var):
                    return np.full((len(HORIZONS), _n_members, len(pres_level_nums), n_stations), np.nan, dtype=np.float32)
                nan_pres = np.full((_n_members, len(pres_level_nums), n_stations), np.nan, dtype=np.float32)
                steps = []
                for task in pres_tasks[var]:
                    r = task.result() if not task.cancelled() else None
                    steps.append(
                        r if isinstance(r, np.ndarray) and r.shape == nan_pres.shape
                        else nan_pres
                    )
                return np.stack(steps, axis=0)

            u_10m = surf_array("U_10M")
            v_10m = surf_array("V_10M")
            vmax_10m = surf_array("VMAX_10M")
            t_2m = surf_array("T_2M")
            td_2m = surf_array("TD_2M")
            pmsl = surf_array("PMSL")
            tot_prec = surf_array("TOT_PREC")
            u_pl = pres_array("U")
            v_pl = pres_array("V")
            w_pl = pres_array("W")
            # W's 3D files (~1.8 GB each) are now deleted per-horizon by _fetch_step's
            # delete_after_read=True as soon as each is downloaded and station-extracted,
            # instead of all HORIZONS worth lingering on disk until this point.

            def deaccum(arr: np.ndarray) -> np.ndarray:
                out = np.empty_like(arr)
                for s in range(n_stations):
                    out[:, :, s] = _deaccumulate(arr[:, :, s])
                return out

            prec_rate    = np.clip(deaccum(tot_prec), 0.0, None)
            rh       = _compute_rh_from_td(t_2m, td_2m)
            t_c      = t_2m - 273.15
            pmsl_hpa = pmsl / 100.0

            # Interpolate U/V/W from the ~80 model levels onto the fixed MAMSL bands, using
            # each station's model-level heights (from HHL). Heights are static across
            # horizons, so collapse (horizon, member) into one axis for a single vectorised
            # interpolation per variable. Bands below a station's terrain come back NaN.
            alt_m_order = ALTITUDE_TARGETS_M
            targets_m = np.array(ALTITUDE_TARGETS_M, dtype=np.float64)
            n_alt = len(alt_m_order)
            if _GRID_LEVEL_HEIGHTS is not None and u_pl.shape[2] == _GRID_LEVEL_HEIGHTS.shape[0]:
                z_stn = _GRID_LEVEL_HEIGHTS[:, station_flat_indices]          # (L, S)
                _H, _M, _L, _S = u_pl.shape
                def _to_alt(field: np.ndarray) -> np.ndarray:
                    return _interp_to_heights(
                        field.reshape(_H * _M, _L, _S), z_stn, targets_m
                    ).reshape(_H, _M, n_alt, _S)
                u_alt = _to_alt(u_pl)
                v_alt = _to_alt(v_pl)
                w_alt = _to_alt(w_pl)
                logger.info("CH1 altitude winds: interpolated U/V/W to %d MAMSL bands", n_alt)
            else:
                _shp = (len(HORIZONS), _n_members, n_alt, n_stations)
                u_alt = np.full(_shp, np.nan, dtype=np.float32)
                v_alt = np.full(_shp, np.nan, dtype=np.float32)
                w_alt = np.full(_shp, np.nan, dtype=np.float32)
                logger.warning(
                    "CH1 altitude winds: no level heights or level-count mismatch "
                    "(pres levels=%s, heights=%s) — bands null",
                    u_pl.shape[2], None if _GRID_LEVEL_HEIGHTS is None else _GRID_LEVEL_HEIGHTS.shape[0],
                )

            def _build_and_cache_stations() -> None:
                """~8.7k StationForecastHour + ~78k AltitudeWindLevel Pydantic objects and
                ~296k compute_stats calls for CH2-sized runs — real CPU time that would
                otherwise block the event loop for its whole duration. Only the set_*
                calls touch shared state (same rule as the v0.3.5 grid-build fix); every
                other local here is built fresh per call.
                """
                for s_idx, station in enumerate(stations):
                    station_id = station["station_id"]

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
                        for alt_idx, alt_m in enumerate(alt_m_order):
                            pl_ws_ev, pl_wd_ev = _wind_ensemble_value(
                                u_alt[h_idx, :, alt_idx, s_idx], v_alt[h_idx, :, alt_idx, s_idx]
                            )
                            pl_wv_ev = _to_ensemble_value(w_alt[h_idx, :, alt_idx, s_idx])

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

            await asyncio.to_thread(_build_and_cache_stations)

            # Grid collection reads the U/V GRIBs kept in tmpdir. Off the event loop:
            # it parses hundreds of GRIBs (~8 min for CH1) and would otherwise block
            # uvicorn for that whole window — /health included. eccodes (via cffi) and
            # numpy release the GIL, so the loop keeps serving while this runs.
            try:
                await asyncio.to_thread(self.collect_grid, ref_dt, tmpdir)
            except Exception as exc:
                logger.exception("Grid collection failed — station data unaffected")
                # Not collection_state.mark_failed(): stations did succeed, and that would
                # mislabel the whole run. Telemetry is what makes a no-grids run visible on
                # the dashboard instead of reporting fully successful.
                _telemetry.record_download_error("ch1", "grid", 0, str(exc))

        logger.info("IconCh1EpsCollector.collect() complete — %d stations", len(stations))
