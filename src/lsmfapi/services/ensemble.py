from typing import TypedDict

import numpy as np


class EnsembleStats(TypedDict):
    probable: float
    min: float
    max: float


def compute_stats(values: np.ndarray | list[float]) -> EnsembleStats:
    # asarray, not array: skips a copy when the caller already has an ndarray (the common
    # case — callers used to do `arr.tolist()` then rebuild an array here on every one of
    # ~296k calls per CH2 run).
    arr = np.asarray(values, dtype=float)
    if np.all(np.isnan(arr)):
        # An all-NaN ensemble is expected (e.g. every member failed for this step) — skip
        # the reducers entirely rather than let them warn on a slice with nothing valid.
        nan = float("nan")
        return EnsembleStats(probable=nan, min=nan, max=nan)
    return EnsembleStats(
        probable=float(np.nanmedian(arr)),
        min=float(np.nanmin(arr)),
        max=float(np.nanmax(arr)),
    )


def compute_wind_direction_stats(angles_deg: np.ndarray | list[float]) -> EnsembleStats:
    if np.all(np.isnan(angles_deg)):
        nan = float("nan")
        return EnsembleStats(probable=nan, min=nan, max=nan)
    angles_arr = np.asarray(angles_deg, dtype=float)
    rad = np.deg2rad(angles_arr)
    probable = float(
        np.rad2deg(np.arctan2(np.nanmedian(np.sin(rad)), np.nanmedian(np.cos(rad)))) % 360
    )
    # Circular min/max: offset each member from the median (wrapped to [-180, 180)), then
    # re-add the extremes to the median. Plain min()/max() on raw degrees is both not
    # NaN-safe and meaningless for a circular quantity — members at 359°/1° would report
    # a fake 358° spread instead of the true 2°.
    offsets = (angles_arr - probable + 180.0) % 360.0 - 180.0
    return EnsembleStats(
        probable=probable,
        min=float((probable + np.nanmin(offsets)) % 360.0),
        max=float((probable + np.nanmax(offsets)) % 360.0),
    )
