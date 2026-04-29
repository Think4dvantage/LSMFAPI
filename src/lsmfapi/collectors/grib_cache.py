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
from contextlib import AbstractContextManager
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_BASE = Path("/tmp/lsmfapi_grib")


def _run_dir(model: str, ref_dt: datetime) -> Path:
    d = _BASE / model / ref_dt.strftime("%Y%m%dT%H%MZ")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _purge_stale(model: str, ref_dt: datetime) -> None:
    """Remove GRIB dirs from previous runs of this model."""
    base = _BASE / model
    if not base.exists():
        return
    current = ref_dt.strftime("%Y%m%dT%H%MZ")
    for child in base.iterdir():
        if child.is_dir() and child.name != current:
            shutil.rmtree(child, ignore_errors=True)
            logger.info("Purged stale GRIB cache: %s", child)


@contextlib.contextmanager
def grib_run_dir(model: str, ref_dt: datetime):
    """Context manager yielding a persistent GRIB directory for *model* / *ref_dt*.

    On entry  — purges directories from previous runs and creates/reuses the
                directory for the current ref_dt.
    On exit   — does **not** delete the directory; files remain for the next
                container start if ref_dt has not changed.
    """
    _purge_stale(model, ref_dt)
    yield _run_dir(model, ref_dt)
