from typing import TypedDict

import numpy as np


class EnsembleStats(TypedDict):
    probable: float
    min: float
    max: float


def compute_stats(values: list[float]) -> EnsembleStats:
    arr = np.array(values, dtype=float)
    return EnsembleStats(
        probable=float(np.nanmedian(arr)),
        min=float(np.nanmin(arr)),
        max=float(np.nanmax(arr)),
    )


def compute_wind_direction_stats(angles_deg: list[float]) -> EnsembleStats:
    rad = np.deg2rad(angles_deg)
    probable = float(
        np.rad2deg(np.arctan2(np.nanmedian(np.sin(rad)), np.nanmedian(np.cos(rad)))) % 360
    )
    return EnsembleStats(
        probable=probable,
        min=float(min(angles_deg)),
        max=float(max(angles_deg)),
    )
