"""Persistent per-run GRIB file cache.

Instead of a throwaway ``tempfile.TemporaryDirectory``, each collection run
uses a directory keyed by ``(model, ref_dt)``.  Files downloaded in one
process stay on disk so that a container restart with the same ref_dt can
skip all HTTP downloads and read straight from the local files.

Old runs (different ref_dt) are cleaned up when a new run starts.
"""

import contextlib
import logging
import shutil
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# Below this, a CH2 run (~480 GB peak, see architecture.md) risks exhausting the volume —
# the same disk-full class of incident that already took the host down once (v0.3.6).
_LOW_SPACE_THRESHOLD_GB = 550

# This module takes the resolved GRIB cache dir as a parameter rather than calling
# get_config() itself. Collectors resolve config through their own subclass module (see
# _icon_eps_base.py's _cfg()) specifically so test monkeypatching of get_config keeps
# working — a hidden get_config() call in here would bypass that entirely.


def log_startup_status(grib_cache_dir: str) -> None:
    """Log the resolved GRIB cache dir and warn if the volume is low on space."""
    base = Path(grib_cache_dir)
    base.mkdir(parents=True, exist_ok=True)
    logger.info("GRIB cache dir: %s", base)
    try:
        free_gb = shutil.disk_usage(base).free / 1_073_741_824
    except OSError:
        logger.warning("Could not stat free space for GRIB cache dir %s", base)
        return
    if free_gb < _LOW_SPACE_THRESHOLD_GB:
        logger.warning(
            "GRIB cache volume has only %.1f GB free (below %d GB threshold) — "
            "a CH2 run can peak at ~480 GB",
            free_gb, _LOW_SPACE_THRESHOLD_GB,
        )
    else:
        logger.info("GRIB cache volume free space: %.1f GB", free_gb)


def _run_dir(model: str, ref_dt: datetime, grib_cache_dir: str) -> Path:
    d = Path(grib_cache_dir) / model / ref_dt.strftime("%Y%m%dT%H%MZ")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _purge_stale(model: str, ref_dt: datetime, grib_cache_dir: str) -> None:
    """Remove GRIB dirs from previous runs of this model."""
    base = Path(grib_cache_dir) / model
    if not base.exists():
        return
    current = ref_dt.strftime("%Y%m%dT%H%MZ")
    for child in base.iterdir():
        if child.is_dir() and child.name != current:
            shutil.rmtree(child, ignore_errors=True)
            logger.info("Purged stale GRIB cache: %s", child)


@contextlib.contextmanager
def grib_run_dir(model: str, ref_dt: datetime, grib_cache_dir: str):
    """Context manager yielding a persistent GRIB directory for *model* / *ref_dt*.

    On entry  — purges directories from previous runs and creates/reuses the
                directory for the current ref_dt.
    On exit   — does **not** delete the directory; files remain for the next
                container start if ref_dt has not changed.
    """
    _purge_stale(model, ref_dt, grib_cache_dir)
    yield _run_dir(model, ref_dt, grib_cache_dir)
