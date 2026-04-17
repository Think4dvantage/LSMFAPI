"""ICON-CH2-EPS collector — 30–120 h, 2 runs/day (00Z/12Z), 21 members.

Identical logic to IconCh1EpsCollector; differs only in:
  - Collection ID
  - Member count (21)
  - Horizon range (30 h … 120 h, 3-hour steps)
  - Grid constants file
  - Run cadence (00Z / 12Z only)
"""

import asyncio
import logging
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import numpy as np
from scipy.spatial import cKDTree

from lsmfapi.collectors.base import BaseCollector
from lsmfapi.collectors.icon_ch1_eps import (
    ACCUM_VARS,
    ALTITUDE_TO_HPA,
    DOWNLOAD_CONCURRENCY,
    PRESSURE_VARS,
    SURFACE_VARS,
    _compute_rh_from_td,
    _deaccumulate,
    _extract_station,
    _horizon_str,
    _read_grid_coords,
    _read_grib2_eccodes,
    _search_item_url,
    _to_ensemble_value,
    _wind_ensemble_value,
)
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

logger = logging.getLogger(__name__)

# ---------- Module-level grid singleton (separate from CH1) ----------
_GRID_TREE: cKDTree | None = None

# ---------- Collection constants ----------
COLLECTION = "ch.meteoschweiz.ogd-forecasting-icon-ch2"
N_MEMBERS = 21
# CH2-EPS: 30 h … 120 h in 3-hour steps
HORIZONS = list(range(30, 121, 3))


def _latest_ref_dt_ch2() -> datetime:
    """Return the most recent CH2-EPS run time that is likely already published.

    CH2-EPS runs every 12 h (00Z/12Z). MeteoSwiss typically publishes data
    ~2–3 hours after initialisation. We use a 3-hour guard.
    """
    now = datetime.now(timezone.utc)
    hour = 0 if now.hour < 12 else 12
    candidate = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if (now - candidate).total_seconds() < 3 * 3600:
        candidate -= timedelta(hours=12)
    return candidate


class IconCh2EpsCollector(BaseCollector):
    """ICON-CH2-EPS collector — 30–120 h, 2 runs/day (00Z/12Z), 21 members."""

    async def _ensure_grid(self, tmpdir: Path) -> None:
        global _GRID_TREE
        if _GRID_TREE is not None:
            return

        cfg = get_config()
        logger.info("Fetching collection metadata for CH2 grid constants")
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

        dest = tmpdir / "horizontal_constants_ch2.grib2"
        await self.download(constants_url, str(dest))

        lats, lons = _read_grid_coords(dest)
        flat_coords = np.column_stack([lats, lons])
        _GRID_TREE = cKDTree(flat_coords)
        logger.info("CH2 KD-tree built: %d grid points", len(lats))

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
    ) -> np.ndarray | None:
        cfg = get_config()
        async with semaphore:
            try:
                url = await _search_item_url(
                    client, cfg.meteoswiss.stac_base_url, COLLECTION,
                    ref_dt, variable, horizon_h,
                )
            except Exception as exc:
                logger.error("CH2 STAC search error %s h=%d: %s", variable, horizon_h, exc)
                return None

            if url is None:
                return None

            dest = tmpdir / f"{variable}_{horizon_h:03d}.grib2"
            try:
                await self.download(url, str(dest))
            except Exception as exc:
                logger.error("CH2 download failed %s h=%d: %s", variable, horizon_h, exc)
                return None

        try:
            arr, _level_coords = _read_grib2_eccodes(dest)
            if arr is None:
                logger.warning("CH2 eccodes returned None for %s h=%d", variable, horizon_h)
                return None
            result = np.stack(
                [_extract_station(arr, int(idx)) for idx in station_flat_indices],
                axis=-1,
            )
            logger.debug("CH2 data %s h=%d shape=%s", variable, horizon_h, result.shape)
            return result
        finally:
            dest.unlink(missing_ok=True)

    async def collect(self) -> None:  # noqa: C901
        ref_dt = _latest_ref_dt_ch2()
        logger.info("IconCh2EpsCollector.collect() ref_dt=%s", ref_dt.isoformat())

        with tempfile.TemporaryDirectory(prefix="lsmfapi_ch2_") as tmpdir_str:
            tmpdir = Path(tmpdir_str)

            await self._ensure_grid(tmpdir)
            stations = await self._fetch_stations()
            if not stations:
                logger.warning("No stations returned; skipping CH2 collection")
                return

            n_stations = len(stations)
            station_lats = np.array([s["latitude"] for s in stations])
            station_lons = np.array([s["longitude"] for s in stations])
            station_coords = np.column_stack([station_lats, station_lons])
            _, station_flat_indices = _GRID_TREE.query(station_coords)

            semaphore = asyncio.Semaphore(DOWNLOAD_CONCURRENCY)
            cfg = get_config()

            async with httpx.AsyncClient(timeout=300) as client:

                # Pressure level coords: probe U at first horizon
                level_hpa: np.ndarray | None = None
                u0_url = await _search_item_url(
                    client, cfg.meteoswiss.stac_base_url, COLLECTION, ref_dt, "U", HORIZONS[0]
                )
                if u0_url:
                    dest = tmpdir / "U_probe_ch2.grib2"
                    try:
                        await self.download(u0_url, str(dest))
                        _, level_hpa = _read_grib2_eccodes(dest)
                    except Exception as exc:
                        logger.warning("CH2 pressure level probe failed: %s", exc)
                    finally:
                        dest.unlink(missing_ok=True)

                if level_hpa is None:
                    logger.info("No CH2 pressure-level data — skipping U/V/W")
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
                            logger.info("CH2 %d/%d (%s h=%d)", done, total, var, h)

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
            logger.info("CH2 surface fetch: %d/%d tasks returned data", n_surf_ok, n_surf_total)

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

            u_10m = surf_array("U_10M");  v_10m = surf_array("V_10M")
            vmax_10m = surf_array("VMAX_10M")
            t_2m = surf_array("T_2M");    td_2m = surf_array("TD_2M")
            pmsl = surf_array("PMSL")
            tot_prec = surf_array("TOT_PREC");    dursun = surf_array("DURSUN")
            aswdir_s = surf_array("ASWDIR_S");    aswdifd_s = surf_array("ASWDIFD_S")
            clct = surf_array("CLCT");  clcl = surf_array("CLCL")
            clcm = surf_array("CLCM");  clch = surf_array("CLCH")
            hbas_con = surf_array("HBAS_CON");    hpbl = surf_array("HPBL")
            hzerocl = surf_array("HZEROCL");      cape_ml = surf_array("CAPE_ML")
            cin_ml = surf_array("CIN_ML")
            u_pl = pres_array("U");  v_pl = pres_array("V");  w_pl = pres_array("W")

            def deaccum(arr: np.ndarray) -> np.ndarray:
                out = np.empty_like(arr)
                for s in range(n_stations):
                    out[:, :, s] = _deaccumulate(arr[:, :, s])
                return out

            prec_rate = np.clip(deaccum(tot_prec), 0.0, None)
            dursun_min = np.clip(deaccum(dursun) / 60.0, 0.0, None)
            solar_direct = np.clip(deaccum(aswdir_s) / 3600.0, 0.0, None)
            solar_diffuse = np.clip(deaccum(aswdifd_s) / 3600.0, 0.0, None)
            rh = _compute_rh_from_td(t_2m, td_2m)
            t_c = t_2m - 273.15
            pmsl_hpa = pmsl / 100.0

            level_indices: dict[int, int] = {int(round(h)): i for i, h in enumerate(level_hpa)}
            alt_m_order = sorted(ALTITUDE_TO_HPA.keys())
            now = datetime.now(timezone.utc)

            for s_idx, station in enumerate(stations):
                station_id = station["station_id"]
                lat = float(station["latitude"])
                lon = float(station["longitude"])
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
                logger.info("CH2 cached forecast for %s (%d hours)", station_id, len(hours_list))

        logger.info("IconCh2EpsCollector.collect() complete — %d stations", len(stations))
