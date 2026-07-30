"""Unit tests for pure-numpy helper functions in collectors/icon_ch1_eps.py.

These do not hit the network or eccodes GRIB parsing — only numpy math — but the module
itself imports eccodes/scipy at load time, so this file needs the full poetry environment
(same as the integration test), unlike test_ensemble.py / test_cache_persistence.py.
"""

import warnings
from datetime import datetime, timezone

import numpy as np
import pytest

import lsmfapi.collectors.icon_ch1_eps as ch1_mod

# Model levels top->bottom (as ICON delivers them): heights descending [3000, 2000, 1000, 0]
_LEVELS = np.array([[3000.0], [2000.0], [1000.0], [0.0]])
_FIELD = np.array([[[10.0], [20.0], [30.0], [40.0]]])  # (M=1, L=4, N=1)


def test_interp_to_heights_identity():
    """Target exactly at a model level must return that level's value unchanged."""
    out = ch1_mod._interp_to_heights(_FIELD, _LEVELS, np.array([1000.0]))
    assert out[0, 0, 0] == pytest.approx(30.0)


def test_interp_to_heights_linear_weight():
    """Halfway between two levels must average their values exactly."""
    out = ch1_mod._interp_to_heights(_FIELD, _LEVELS, np.array([1500.0]))
    assert out[0, 0, 0] == pytest.approx(25.0)


def test_interp_to_heights_below_terrain_is_null():
    out = ch1_mod._interp_to_heights(_FIELD, _LEVELS, np.array([-100.0]))
    assert np.isnan(out[0, 0, 0])


def test_interp_to_heights_above_top_is_null():
    out = ch1_mod._interp_to_heights(_FIELD, _LEVELS, np.array([5000.0]))
    assert np.isnan(out[0, 0, 0])


def test_interp_to_heights_level_count_mismatch_returns_all_nan():
    """A level_heights/field shape mismatch must not silently misalign — return all-NaN."""
    mismatched_levels = np.array([[3000.0], [2000.0], [1000.0]])  # 3 rows, field has 4
    out = ch1_mod._interp_to_heights(_FIELD, mismatched_levels, np.array([1000.0]))
    assert out.shape == (1, 1, 1)
    assert np.all(np.isnan(out))


def test_deaccumulate():
    arr = np.array([[10.0], [15.0], [25.0]])
    out = ch1_mod._deaccumulate(arr)
    np.testing.assert_allclose(out.ravel(), [10.0, 5.0, 10.0])


def test_deaccumulate_nan_first_step_does_not_warn():
    """arr[:1,:] * 0 turns NaN*0 into NaN, silently breaking the zero-baseline intent —
    zeros_like must not multiply by the (possibly NaN) data at all, and must not warn.
    """
    arr = np.array([[np.nan], [15.0], [25.0]])
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        out = ch1_mod._deaccumulate(arr)
    assert np.isnan(out[0, 0])
    assert np.isnan(out[1, 0])  # arr[1]-arr[0] with arr[0]=NaN is unavoidably NaN
    assert out[2, 0] == 10.0  # unaffected — the NaN doesn't propagate past step 1


def test_compute_rh_from_td_saturated_is_100_percent():
    rh = ch1_mod._compute_rh_from_td(np.array([293.15]), np.array([293.15]))
    assert rh[0] == pytest.approx(100.0, abs=0.5)


def test_compute_rh_from_td_known_value():
    """T=20C, TD=10C -> RH ~= 52.5% (Magnus formula reference value)."""
    rh = ch1_mod._compute_rh_from_td(np.array([293.15]), np.array([283.15]))
    assert rh[0] == pytest.approx(52.5, abs=0.5)


def test_compute_rh_from_td_clipped_to_valid_range():
    # TD > T is unphysical but must clip rather than return >100%
    rh = ch1_mod._compute_rh_from_td(np.array([283.15]), np.array([293.15]))
    assert rh[0] <= 100.0


def _patch_now(monkeypatch, fixed_now: datetime) -> None:
    class _FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    monkeypatch.setattr(ch1_mod, "datetime", _FakeDateTime)


def test_latest_ref_dt_at_exact_2h_boundary_uses_current_slot(monkeypatch):
    """Exactly 2h after a release, the guard must NOT push back a further slot."""
    _patch_now(monkeypatch, datetime(2026, 1, 1, 14, 0, 0, tzinfo=timezone.utc))
    assert ch1_mod._latest_ref_dt() == datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def test_latest_ref_dt_just_inside_guard_falls_back_a_slot(monkeypatch):
    """This exact boundary (1:59:59 short of 2h) caused a v0.3.1 production data-loss bug."""
    _patch_now(monkeypatch, datetime(2026, 1, 1, 13, 59, 59, tzinfo=timezone.utc))
    assert ch1_mod._latest_ref_dt() == datetime(2026, 1, 1, 6, 0, 0, tzinfo=timezone.utc)


def test_latest_ref_dt_at_midnight_wraps_to_previous_day(monkeypatch):
    _patch_now(monkeypatch, datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc))
    assert ch1_mod._latest_ref_dt() == datetime(2025, 12, 31, 18, 0, 0, tzinfo=timezone.utc)
