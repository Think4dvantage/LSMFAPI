"""Unit tests for services/ensemble.py — pure numpy, no network, no eccodes."""

import math
import warnings

from lsmfapi.services.ensemble import compute_stats, compute_wind_direction_stats


def test_compute_stats_basic():
    stats = compute_stats([1.0, 2.0, 3.0])
    assert stats["probable"] == 2.0
    assert stats["min"] == 1.0
    assert stats["max"] == 3.0


def test_compute_stats_skips_nan():
    stats = compute_stats([1.0, 2.0, 3.0, float("nan")])
    assert stats["probable"] == 2.0
    assert stats["min"] == 1.0
    assert stats["max"] == 3.0


def test_compute_wind_direction_stats_no_wraparound():
    """No wraparound: circular fix must reduce to plain min/max/median."""
    stats = compute_wind_direction_stats([80.0, 100.0, 120.0])
    assert stats["probable"] == 100.0
    assert stats["min"] == 80.0
    assert stats["max"] == 120.0


def test_compute_wind_direction_stats_wraparound():
    """Members straddling 0/360 must report the true ~2 deg spread, not ~358 deg."""
    stats = compute_wind_direction_stats([359.0, 1.0])
    assert stats["min"] == 359.0
    assert stats["max"] == 1.0
    # probable is circularly equivalent to 0/360
    assert stats["probable"] % 360 == 0.0


def test_compute_wind_direction_stats_nan_safe():
    stats = compute_wind_direction_stats([10.0, float("nan"), 350.0])
    assert not math.isnan(stats["min"])
    assert not math.isnan(stats["max"])
    assert not math.isnan(stats["probable"])


def test_compute_stats_all_nan_returns_nan_without_warning():
    """An all-NaN ensemble (every member failed) is expected, not a bug — must not warn."""
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        stats = compute_stats([float("nan"), float("nan")])
    assert math.isnan(stats["probable"])
    assert math.isnan(stats["min"])
    assert math.isnan(stats["max"])


def test_compute_wind_direction_stats_all_nan_returns_nan_without_warning():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        stats = compute_wind_direction_stats([float("nan"), float("nan")])
    assert math.isnan(stats["probable"])
    assert math.isnan(stats["min"])
    assert math.isnan(stats["max"])
