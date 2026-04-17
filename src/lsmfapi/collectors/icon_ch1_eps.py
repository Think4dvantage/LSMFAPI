import asyncio
import logging
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import eccodes
import httpx
import numpy as np
from scipy.spatial import cKDTree

from lsmfapi.collectors.base import BaseCollector
from lsmfapi.config import get_config
from lsmfapi.database.cache import set_station_altitude_winds, set_station_forecast
from lsmfapi.models.forecast import (
    AltitudeWindsPoint,
    AltitudeWindsResponse,
    EnsembleValue,
    ForecastPoint,
    ForecastResponse,
    PressureLevelWinds,
)
from lsmfapi.services.ensemble import compute_stats, compute_wind_direction_stats

logger = logging.getLogger(__name__)

# ---------- Module-level grid singleton (built once per process) ----------
_GRID_TREE: cKDTree | None = None

# ---------- Collection constants ----------
COLLECTION = "ch.meteoschweiz.ogd-forecasting-icon-ch1"
N_MEMBERS = 11
HORIZONS = list(range(31))  # 0 h … 30 h inclusive

SURFACE_VARS: list[str] = [
    "U_10M", "V_10M", "VMAX_10M",
    "T_2M", "TD_2M", "PMSL",
    "TOT_PREC", "DURSUN", "ASWDIR_S", "ASWDIFD_S",
    "CLCT", "CLCL", "CLCM", "CLCH",
    "HBAS_CON", "HPBL", "HZEROCL", "CAPE_ML", "CIN_ML",
]
ACCUM_VARS: frozenset[str] = frozenset({"TOT_PREC", "DURSUN", "ASWDIR_S", "ASWDIFD_S"})

PRESSURE_VARS: list[str] = ["U", "V", "W"]
ALTITUDE_TO_HPA: dict[int, int] = {
    500: 950, 800: 920, 1000: 900, 1500: 850, 2000: 800,
    2500: 750, 3000: 700, 4000: 600, 5000: 500,
}

DOWNLOAD_CONCURRENCY = 6


# ---------- Pure helpers ----------

def _horizon_str(h: int) -> str:
    return f"P0DT{h:02d}H00M00S"


def _parse_horizon_h(s: str) -> int:
    """Parse ISO 8601 duration → integer hours. Returns -1 on failure.

    Handles both 'P0DT06H00M00S' (with days) and 'PT6H' (no days) forms.
    """
    # Form with days: P[n]DT[n]H...
    m = re.match(r"P(\d+)DT(\d+)H", s)
    if m:
        return int(m.group(1)) * 24 + int(m.group(2))
    # Form without days: PT[n]H...
    m = re.match(r"PT(\d+)H", s)
    if m:
        return int(m.group(1))
    return -1


async def _search_item_url(
    client: httpx.AsyncClient,
    stac_base_url: str,
    collection: str,
    ref_dt: datetime,
    variable: str,
    horizon_h: int,
    perturbed: bool = True,
) -> str | None:
    """Return the download URL for one (variable, horizon) pair, or None if not available."""
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
    """Return the most recent CH1-EPS run time that is likely already published.

    CH1-EPS runs every 6 h (00Z/06Z/12Z/18Z). MeteoSwiss typically publishes
    data ~90 min after initialisation. We use a 2-hour guard so that a run
    started at, say, 18:00Z is not selected until 20:00Z.
    """
    now = datetime.now(timezone.utc)
    hour = (now.hour // 6) * 6
    candidate = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if (now - candidate).total_seconds() < 2 * 3600:
        candidate -= timedelta(hours=6)
    return candidate


def _deaccumulate(arr: np.ndarray) -> np.ndarray:
    """Difference accumulated field along steps axis (axis=0).
    arr shape: (n_steps, n_members) — returns per-step delta."""
    return np.diff(arr, axis=0, prepend=arr[:1, :] * 0)


def _compute_rh_from_td(t_k: np.ndarray, td_k: np.ndarray) -> np.ndarray:
    """Relative humidity from T_2M and TD_2M (both in Kelvin), Magnus formula."""
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
    return EnsembleValue(**compute_stats(speeds.tolist())), \
           EnsembleValue(**compute_wind_direction_stats(directions.tolist()))


# ---------- eccodes-based GRIB2 readers ----------

def _eccodes_get(msg, key: str, default=None):
    """Get a scalar key from an eccodes message, returning default on error."""
    try:
        return eccodes.codes_get(msg, key)
    except eccodes.CodesInternalError:
        return default


def _read_grid_coords(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """Read geographic lat/lon from a COSMO/ICON horizontal_constants GRIB2.

    For ICON unstructured grids the `latitudes`/`longitudes` eccodes keys are
    unavailable.  Instead the file contains RLAT and RLON messages whose *data
    values* are the geographic coordinates of every grid point, stored in
    radians (COSMO convention).

    Returns (lats, lons) as 1-D float64 arrays of length N_grid_points.
    """
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

    # COSMO stores coordinates in radians → convert to degrees.
    # Guard: if values already exceed ±(π/2 + ε) they are already in degrees.
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


def _read_grib2_eccodes(
    path: Path,
) -> tuple[np.ndarray | None, np.ndarray | None]:
    """Read a forecast GRIB2 file with eccodes, returning (values, level_hpa).

    values shape:
      Surface  : (n_members, n_points)
      Multi-lev: (n_members, n_levels, n_points)
    level_hpa  : None for surface, ndarray of hPa for pressure-level files.

    Returns (None, None) on failure.
    """
    messages: list[tuple[int, int, np.ndarray]] = []  # (member, level, values)

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
                    messages.append((member, level, values))
                finally:
                    eccodes.codes_release(msg)
    except Exception as exc:
        logger.error("eccodes read failed for %s: %s", path.name, exc)
        return None, None

    if not messages:
        logger.warning("No GRIB2 messages in %s", path.name)
        return None, None

    unique_members = sorted({m[0] for m in messages})
    unique_levels  = sorted({m[1] for m in messages})
    n_points = len(messages[0][2])

    member_idx = {m: i for i, m in enumerate(unique_members)}
    level_idx  = {l: i for i, l in enumerate(unique_levels)}

    if len(unique_levels) == 1:
        arr = np.full((len(unique_members), n_points), np.nan, dtype=np.float32)
        for member, level, values in messages:
            arr[member_idx[member]] = values
        return arr, None
    else:
        arr = np.full(
            (len(unique_members), len(unique_levels), n_points), np.nan, dtype=np.float32
        )
        for member, level, values in messages:
            arr[member_idx[member], level_idx[level]] = values
        return arr, np.array(unique_levels, dtype=float)


def _extract_station(arr: np.ndarray, flat_idx: int) -> np.ndarray:
    """Extract values at one station using its flat grid index.

    arr shape: (n_members, n_points) or (n_members, n_levels, n_points)
    Returns  : (n_members,)          or (n_members, n_levels)
    """
    return arr[..., flat_idx]


# ---------- Collector ----------

class IconCh1EpsCollector(BaseCollector):
    """ICON-CH1-EPS collector — 0–30 h, 4 runs/day (00Z/06Z/12Z/18Z), 11 members."""

    # ------------------------------------------------------------------ #
    # Grid constants                                                       #
    # ------------------------------------------------------------------ #

    async def _ensure_grid(self, tmpdir: Path) -> None:
        global _GRID_TREE
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
        flat_coords = np.column_stack([lats, lons])
        _GRID_TREE = cKDTree(flat_coords)
        logger.info("KD-tree built: %d grid points", len(lats))

    # ------------------------------------------------------------------ #
    # Station list                                                         #
    # ------------------------------------------------------------------ #

    async def _fetch_stations(self) -> list[dict]:
        cfg = get_config()
        async with httpx.AsyncClient(timeout=30, verify=False) as client:
            resp = await client.get(f"{cfg.lenticularis.base_url}/api/stations")
            resp.raise_for_status()
            return resp.json()

    # ------------------------------------------------------------------ #
    # Per-(variable, step) fetch                                          #
    # ------------------------------------------------------------------ #

    async def _fetch_step(
        self,
        semaphore: asyncio.Semaphore,
        client: httpx.AsyncClient,
        ref_dt: datetime,
        variable: str,
        horizon_h: int,
        station_flat_indices: np.ndarray,
        tmpdir: Path,
    ) -> np.ndarray | None:
        cfg = get_config()
        async with semaphore:
            try:
                url = await _search_item_url(
                    client, cfg.meteoswiss.stac_base_url, COLLECTION,
                    ref_dt, variable, horizon_h,
                )
            except Exception as exc:
                logger.error("STAC search error %s h=%d: %s", variable, horizon_h, exc)
                return None

            if url is None:
                return None  # variable not available at this horizon — expected, no log

            dest = tmpdir / f"{variable}_{horizon_h:03d}.grib2"
            try:
                await self.download(url, str(dest))
            except Exception as exc:
                logger.error("Download failed %s h=%d: %s", variable, horizon_h, exc)
                return None

        try:
            arr, _level_coords = _read_grib2_eccodes(dest)
            if arr is None:
                logger.warning("eccodes returned None for %s h=%d", variable, horizon_h)
                return None
            result = np.stack(
                [_extract_station(arr, int(idx)) for idx in station_flat_indices],
                axis=-1,
            )
            logger.debug("CH1 data %s h=%d shape=%s", variable, horizon_h, result.shape)
            return result
        finally:
            dest.unlink(missing_ok=True)

    # ------------------------------------------------------------------ #
    # Main collect                                                         #
    # ------------------------------------------------------------------ #

    async def collect(self) -> None:  # noqa: C901
        ref_dt = _latest_ref_dt()
        logger.info("IconCh1EpsCollector.collect() ref_dt=%s", ref_dt.isoformat())

        with tempfile.TemporaryDirectory(prefix="lsmfapi_ch1_") as tmpdir_str:
            tmpdir = Path(tmpdir_str)

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

                # Pressure level coords: probe U at h=0
                level_hpa: np.ndarray | None = None
                u0_url = await _search_item_url(
                    client, cfg.meteoswiss.stac_base_url, COLLECTION, ref_dt, "U", HORIZONS[0]
                )
                if u0_url:
                    dest = tmpdir / "U_probe.grib2"
                    try:
                        await self.download(u0_url, str(dest))
                        _, level_hpa = _read_grib2_eccodes(dest)
                    except Exception as exc:
                        logger.warning("Pressure level probe failed: %s", exc)
                    finally:
                        dest.unlink(missing_ok=True)

                if level_hpa is None:
                    logger.info("No pressure-level data for %s — skipping U/V/W", COLLECTION)
                    level_hpa = np.array(sorted(ALTITUDE_TO_HPA.values(), reverse=True), dtype=float)

                has_pressure_levels = u0_url is not None

                n_surf = len(SURFACE_VARS) * len(HORIZONS)
                n_pres = len(PRESSURE_VARS) * len(HORIZONS) if has_pressure_levels else 0
                progress = [0, n_surf + n_pres]

                async def fetch(var: str, h: int) -> np.ndarray | None:
                    try:
                        return await self._fetch_step(
                            semaphore, client, ref_dt, var, h, station_flat_indices, tmpdir
                        )
                    finally:
                        progress[0] += 1
                        done, total = progress
                        if done % 20 == 0 or done == total:
                            logger.info("CH1 %d/%d (%s h=%d)", done, total, var, h)

                surf_tasks: dict[str, list[asyncio.Task]] = {v: [] for v in SURFACE_VARS}
                for var in SURFACE_VARS:
                    for h in HORIZONS:
                        surf_tasks[var].append(asyncio.ensure_future(fetch(var, h)))

                pres_tasks: dict[str, list[asyncio.Task]] = {v: [] for v in PRESSURE_VARS}
                if has_pressure_levels:
                    for var in PRESSURE_VARS:
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

            nan_surf = np.full((N_MEMBERS, n_stations), np.nan)

            def surf_array(var: str) -> np.ndarray:
                steps = []
                for task in surf_tasks[var]:
                    r = task.result() if not task.cancelled() else None
                    if isinstance(r, np.ndarray) and r.ndim == 3:
                        # 3D result (members, model_levels, stations) — take bottom level (surface)
                        r = r[:, -1, :]
                    steps.append(r if isinstance(r, np.ndarray) and r.ndim == 2 else nan_surf)
                return np.stack(steps, axis=0)

            def pres_array(var: str) -> np.ndarray:
                if not pres_tasks[var]:
                    return np.full((len(HORIZONS), N_MEMBERS, len(level_hpa), n_stations), np.nan)
                nan_pres = np.full((N_MEMBERS, len(level_hpa), n_stations), np.nan)
                steps = []
                for task in pres_tasks[var]:
                    r = task.result() if not task.cancelled() else None
                    steps.append(r if isinstance(r, np.ndarray) and r.ndim >= 3 else nan_pres)
                return np.stack(steps, axis=0)

            u_10m = surf_array("U_10M");   v_10m = surf_array("V_10M")
            vmax_10m = surf_array("VMAX_10M")
            t_2m = surf_array("T_2M");     td_2m = surf_array("TD_2M")
            pmsl = surf_array("PMSL")
            tot_prec = surf_array("TOT_PREC");   dursun = surf_array("DURSUN")
            aswdir_s = surf_array("ASWDIR_S");   aswdifd_s = surf_array("ASWDIFD_S")
            clct = surf_array("CLCT");  clcl = surf_array("CLCL")
            clcm = surf_array("CLCM");  clch = surf_array("CLCH")
            hbas_con = surf_array("HBAS_CON");   hpbl = surf_array("HPBL")
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

            level_indices: dict[int, int] = {int(round(h)): i for i, h in enumerate(level_hpa)}
            alt_m_order = sorted(ALTITUDE_TO_HPA.keys())
            now = datetime.now(timezone.utc)

            for s_idx, station in enumerate(stations):
                station_id = station["station_id"]
                lat  = float(station["latitude"])
                lon  = float(station["longitude"])
                elev = int(station["elevation"]) if station.get("elevation") is not None else 0

                hours_list: list[ForecastPoint] = []
                alt_hours: list[AltitudeWindsPoint] = []
                for h_idx, h in enumerate(HORIZONS):
                    valid_time = ref_dt + timedelta(hours=h)

                    def s(arr: np.ndarray) -> EnsembleValue:
                        return _to_ensemble_value(arr[h_idx, :, s_idx])

                    ws, wd = _wind_ensemble_value(u_10m[h_idx, :, s_idx], v_10m[h_idx, :, s_idx])

                    hours_list.append(ForecastPoint(
                        valid_time=valid_time,
                        wind_speed=ws,
                        wind_gusts=s(vmax_10m),
                        wind_direction=wd,
                        temperature=s(t_c),
                        humidity=s(rh),
                        pressure_qff=s(pmsl_hpa),
                        precipitation=s(prec_rate),
                        solar_direct=s(solar_direct),
                        solar_diffuse=s(solar_diffuse),
                        sunshine_minutes=s(dursun_min),
                        cloud_cover_total=s(clct),
                        cloud_cover_low=s(clcl),
                        cloud_cover_mid=s(clcm),
                        cloud_cover_high=s(clch),
                        cloud_base_convective=s(hbas_con),
                        boundary_layer_height=s(hpbl),
                        freezing_level=s(hzerocl),
                        cape=s(cape_ml),
                        cin=s(cin_ml),
                    ))

                    pl_list: list[PressureLevelWinds] = []
                    for alt_m in alt_m_order:
                        l_idx = level_indices.get(ALTITUDE_TO_HPA[alt_m])
                        if l_idx is not None:
                            pl_ws, pl_wd = _wind_ensemble_value(
                                u_pl[h_idx, :, l_idx, s_idx], v_pl[h_idx, :, l_idx, s_idx]
                            )
                            pl_wv = _to_ensemble_value(w_pl[h_idx, :, l_idx, s_idx])
                        else:
                            nan_ev = EnsembleValue(probable=None, min=None, max=None)
                            pl_ws = pl_wd = pl_wv = nan_ev
                        pl_list.append(PressureLevelWinds(
                            altitude_m=alt_m,
                            wind_speed=pl_ws,
                            wind_direction=pl_wd,
                            vertical_wind=pl_wv,
                        ))
                    alt_hours.append(AltitudeWindsPoint(valid_time=valid_time, levels=pl_list))

                set_station_forecast(
                    station_id,
                    ForecastResponse(
                        station_lat=lat,
                        station_lon=lon,
                        station_elevation=elev,
                        generated_at=now,
                        hours=hours_list,
                    ),
                )
                set_station_altitude_winds(
                    station_id,
                    AltitudeWindsResponse(
                        station_lat=lat,
                        station_lon=lon,
                        station_elevation=elev,
                        generated_at=now,
                        hours=alt_hours,
                    ),
                )
                logger.info("Cached forecast for %s (%d hours)", station_id, len(hours_list))

        logger.info("IconCh1EpsCollector.collect() complete — %d stations", len(stations))
