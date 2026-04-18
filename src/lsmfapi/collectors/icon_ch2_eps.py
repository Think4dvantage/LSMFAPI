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
    _ev_flat,
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
    AltitudeWindLevel,
    AltitudeWindsProfile,
    AltitudeWindsResponse,
    EnsembleValue,
    StationForecastHour,
    StationForecastResponse,
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
    """Return most recent CH2-EPS run time that is likely already published (3 h guard)."""
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

            for s_idx, station in enumerate(stations):
                station_id = station["station_id"]
                lat = float(station["latitude"])
                lon = float(station["longitude"])
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
                        model="icon-ch2",
                        source="swissmeteo",
                        forecast=forecast_list,
                    ),
                )
                set_station_altitude_winds(
                    station_id,
                    AltitudeWindsResponse(
                        station_id=station_id,
                        init_time=ref_dt,
                        model="icon-ch2",
                        source="swissmeteo",
                        profiles=profiles_list,
                    ),
                )
                logger.info("CH2 cached forecast for %s (%d hours)", station_id, len(forecast_list))

        logger.info("IconCh2EpsCollector.collect() complete — %d stations", len(stations))
