"""Unit tests for the /grid, /thermal-grid response-size budget guard.

Imports api.routers.forecast, which pulls in icon_ch1_eps (eccodes/scipy) transitively —
same environment requirement as test_collector_helpers.py.
"""

from lsmfapi.api.routers import forecast as forecast_router


def test_budget_error_within_cap_returns_none():
    assert forecast_router._budget_error("wind-grid", 100, 121, 3) is None


def test_budget_error_over_cap_returns_400():
    resp = forecast_router._budget_error("wind-grid", 1_000_000, 121, 36)
    assert resp is not None
    assert resp.status_code == 400


def test_budget_error_derives_field_count_from_caller():
    """Regression guard: the wind-grid call site hardcodes its field count (ws/wd/rh = 3).
    Adding a field there without bumping this constant would silently under-count the
    budget — this test at least pins the boundary math so a cap change is deliberate.
    """
    n_pts, n_frames, n_fields = 100, 121, 3
    cells = n_pts * n_frames * n_fields
    assert cells <= forecast_router._MAX_RESPONSE_CELLS
    assert forecast_router._budget_error("wind-grid", n_pts, n_frames, n_fields) is None

    over_pts = forecast_router._MAX_RESPONSE_CELLS // (n_frames * n_fields) + 1
    assert forecast_router._budget_error("wind-grid", over_pts, n_frames, n_fields) is not None
