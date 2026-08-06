"""ICON-CH1-EPS collector — h0-h33, 4 runs/day (00Z/06Z/12Z/18Z), 11 members.

Shares its STAC/GRIB/ensemble pipeline with IconCh2EpsCollector via
IconEpsCollectorBase (see _icon_eps_base.py). This module keeps only what is genuinely
CH1-specific: the collection id/horizon range/model-name parameters, the module-level
grid singletons (KD-tree, sample indices, model-level heights — CH1 and CH2 build
different-resolution grids and existing tests reset these via direct module-attribute
access), _ensure_grid/collect_grid (which own that state), and the standalone
_latest_ref_dt (imported by api/routers/dashboard.py and used directly in tests).
"""

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import numpy as np
from scipy.spatial import cKDTree

from lsmfapi.collectors._icon_eps_base import (
    ALTITUDE_TARGETS_M,
    GRID_LAT_MAX,
    GRID_LAT_MIN,
    GRID_LON_MAX,
    GRID_LON_MIN,
    GRID_STEP_DEG,
    IconEpsCollectorBase,
    _build_grid_wind_cache,
    _build_thermal_grid_cache,
    _compute_rh_from_td,
    _deaccumulate,
    _interp_to_heights,
    _load_level_heights,
    _read_grid_coords,
)
from lsmfapi.config import get_config
from lsmfapi.database.cache import set_grid_wind_cache, set_thermal_grid_cache

logger = logging.getLogger(__name__)

# Re-exported for backward compatibility: api/routers/forecast.py imports
# ALTITUDE_TARGETS_M/GRID_LAT_MAX/GRID_LAT_MIN/GRID_LON_MAX/GRID_LON_MIN from this module,
# and tests/test_collector_helpers.py calls _interp_to_heights/_deaccumulate/
# _compute_rh_from_td as module attributes (ch1_mod._interp_to_heights(...)). The last
# three have no remaining caller inside this module now that collect()/_fetch_step moved
# to the base class, hence __all__ to keep ruff from flagging them as unused imports.
__all__ = [
    "IconCh1EpsCollector",
    "_interp_to_heights",
    "_deaccumulate",
    "_compute_rh_from_td",
]

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

# ---------- Collection constants ----------
COLLECTION = "ch.meteoschweiz.ogd-forecasting-icon-ch1"
N_MEMBERS = 11  # informational only — actual count is read from each GRIB run
HORIZONS = list(range(34))  # 0 h … 33 h inclusive
# 1.5h, not 2h — matches scheduler.py's 01:30/07:30/13:30/19:30 UTC cron. Lowered 2026-08-06:
# collect()'s _wait_for_full_publish poll (see _icon_eps_base.py) now confirms the run is
# actually published before fetching, so this guard only needs to rule out a run that
# obviously hasn't started publishing at all — the poll handles "started but not finished".
REF_DT_GUARD_HOURS = 1.5


def _latest_ref_dt() -> datetime:
    """Return most recent CH1-EPS run time likely already published (1.5 h guard)."""
    now = datetime.now(timezone.utc)
    hour = (now.hour // 6) * 6
    candidate = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if (now - candidate).total_seconds() < REF_DT_GUARD_HOURS * 3600:
        candidate -= timedelta(hours=6)
    return candidate


class IconCh1EpsCollector(IconEpsCollectorBase):
    """ICON-CH1-EPS collector — h0-h33, 4 runs/day (00Z/06Z/12Z/18Z), 11 members."""

    COLLECTION = COLLECTION
    MODEL_TAG = "ch1"
    MODEL_NAME = "icon-ch1"
    ACCUM_PRIOR_H = None
    REF_DT_GUARD_HOURS = REF_DT_GUARD_HOURS
    GRID_CONSTANTS_PREFIX = "ch1"
    U_PROBE_FILENAME = "U_probe.grib2"
    N_MEMBERS = N_MEMBERS

    @property
    def HORIZONS(self) -> list[int]:
        return HORIZONS

    def _cfg(self):
        return get_config()

    def _compute_ref_dt(self) -> datetime:
        return _latest_ref_dt()

    def _grid_tree(self):
        return _GRID_TREE

    def _grid_level_heights(self):
        return _GRID_LEVEL_HEIGHTS

    async def _ensure_grid(self, tmpdir: Path) -> None:
        global _GRID_TREE
        global _GRID_SAMPLE_INDICES, _GRID_N_LAT, _GRID_N_LON, _GRID_LEVEL_HEIGHTS

        if _GRID_TREE is not None:
            return

        cfg = get_config()
        logger.info("Fetching collection metadata for grid constants")
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(
                f"{cfg.meteoswiss.stac_base_url}/collections/{self.COLLECTION}"
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
                f"horizontal_constants asset not found in collection {self.COLLECTION}"
            )

        dest = tmpdir / f"horizontal_constants_{self.GRID_CONSTANTS_PREFIX}.grib2"
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
                f"vertical_constants asset not found in collection {self.COLLECTION}"
            )
        vc_dest = tmpdir / f"vertical_constants_{self.GRID_CONSTANTS_PREFIX}.grib2"
        await self.download(vertical_url, str(vc_dest))
        _GRID_LEVEL_HEIGHTS = _load_level_heights(vc_dest)
        vc_dest.unlink(missing_ok=True)
        logger.info(
            "CH1 model-level heights: %d levels × %d points, MAMSL range [%.0f, %.0f]",
            _GRID_LEVEL_HEIGHTS.shape[0], _GRID_LEVEL_HEIGHTS.shape[1],
            float(np.nanmin(_GRID_LEVEL_HEIGHTS)), float(np.nanmax(_GRID_LEVEL_HEIGHTS)),
        )

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
            _GRID_SAMPLE_INDICES, _GRID_N_LAT, _GRID_N_LON, self.MODEL_NAME,
        )
        set_grid_wind_cache(cache)
        logger.info(
            "CH1 GridWindCache set: %d × %d points, %d bands, %d frames, init_time=%s",
            _GRID_N_LAT, _GRID_N_LON, len(ALTITUDE_TARGETS_M), len(HORIZONS), ref_dt.isoformat(),
        )
        thermal = _build_thermal_grid_cache(
            HORIZONS, ref_dt, tmpdir,
            _GRID_SAMPLE_INDICES, _GRID_N_LAT, _GRID_N_LON, self.MODEL_NAME,
            accum_prior_h=self.ACCUM_PRIOR_H,
        )
        set_thermal_grid_cache(thermal)
        logger.info(
            "CH1 ThermalGridCache set: %d × %d points, %d frames, init_time=%s",
            _GRID_N_LAT, _GRID_N_LON, len(HORIZONS), ref_dt.isoformat(),
        )
