"""Unit tests for database/cache.py — combined grid store, dirty flags, and the
save->wipe->load npz round trip. Pure numpy/pydantic, no eccodes, no network.
"""

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from lsmfapi.database import cache as db_cache
from lsmfapi.models.forecast import (
    AltitudeWindLevel,
    AltitudeWindsProfile,
    AltitudeWindsResponse,
    GridWindCache,
    StationForecastHour,
    StationForecastResponse,
    ThermalGridCache,
)

_INIT = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def reset_cache_state(tmp_path, monkeypatch):
    monkeypatch.setattr(db_cache, "CACHE_FILE", tmp_path / "cache.json")
    monkeypatch.setattr(db_cache, "GRID_CACHE_FILE", tmp_path / "grid_cache.npz")
    monkeypatch.setattr(db_cache, "THERMAL_GRID_CACHE_FILE", tmp_path / "thermal_grid_cache.npz")
    monkeypatch.setattr(db_cache, "_LEGACY_GRID_FILES", ())
    db_cache._ch1_station_cache = {}
    db_cache._ch2_station_cache = {}
    db_cache._ch1_station_cache_prev = {}
    db_cache._ch1_altitude_winds_cache = {}
    db_cache._ch2_altitude_winds_cache = {}
    db_cache._ch1_altitude_winds_cache_prev = {}
    db_cache._grid_wind_cache = None
    db_cache._thermal_grid_cache = None
    db_cache._grid_wind_model_init = {}
    db_cache._thermal_grid_model_init = {}
    db_cache._grid_wind_dirty = False
    db_cache._thermal_grid_dirty = False
    db_cache._last_populated_at = None
    yield


def _make_grid_wind(model: str, init_time: datetime, valid_times: list, n: int = 4) -> GridWindCache:
    n_frames = len(valid_times)
    return GridWindCache(
        model=model, init_time=init_time,
        lats=np.linspace(47.0, 46.0, n).astype(np.float32),
        lons=np.linspace(7.0, 8.0, n).astype(np.float32),
        n_lat=2, n_lon=2, lat_max=47.0, lon_min=7.0, step_deg=0.01,
        valid_times=valid_times,
        ws={500: np.full((n_frames, n), 10.0, dtype=np.float16)},
        wd={500: np.full((n_frames, n), 180.0, dtype=np.float16)},
        rh=np.full((n_frames, n), 50.0, dtype=np.float16),
    )


def _make_thermal(model: str, init_time: datetime, valid_times: list, n: int = 4) -> ThermalGridCache:
    n_frames = len(valid_times)
    fields = {f: np.full((n_frames, n), 1.0, dtype=np.float16) for f in db_cache._THERMAL_FIELDS}
    return ThermalGridCache(
        model=model, init_time=init_time,
        lats=np.linspace(47.0, 46.0, n).astype(np.float32),
        lons=np.linspace(7.0, 8.0, n).astype(np.float32),
        n_lat=2, n_lon=2, lat_max=47.0, lon_min=7.0, step_deg=0.01,
        valid_times=valid_times,
        **fields,
    )


def test_set_grid_wind_cache_slices_by_horizon():
    """CH1 writes its horizon slice, CH2 writes its own — no gap, no overlap merge needed."""
    ch1_vt = [_INIT]  # horizon 0
    ch2_vt = [_INIT + timedelta(hours=34)]  # horizon 34
    db_cache.set_grid_wind_cache(_make_grid_wind("icon-ch1", _INIT, ch1_vt))
    db_cache.set_grid_wind_cache(_make_grid_wind("icon-ch2", _INIT, ch2_vt))

    merged = db_cache.get_grid_wind_cache()
    assert merged is not None
    assert merged.model == "icon-ch1+ch2"
    assert len(merged.valid_times) == 35  # populated range [0, 35)
    assert merged.valid_times[0] == ch1_vt[0]
    assert merged.valid_times[-1] == ch2_vt[0]
    assert float(merged.ws[500][0, 0]) == pytest.approx(10.0, abs=0.1)


def test_set_grid_wind_cache_marks_dirty():
    assert db_cache._grid_wind_dirty is False
    db_cache.set_grid_wind_cache(_make_grid_wind("icon-ch1", _INIT, [_INIT]))
    assert db_cache._grid_wind_dirty is True


def test_save_cache_clears_dirty_flag_and_skips_untouched_grid(monkeypatch):
    calls = {"thermal": 0}
    orig_save_thermal = db_cache._save_thermal_grid_cache

    def _spy():
        calls["thermal"] += 1
        return orig_save_thermal()

    monkeypatch.setattr(db_cache, "_save_thermal_grid_cache", _spy)

    db_cache.set_grid_wind_cache(_make_grid_wind("icon-ch1", _INIT, [_INIT]))
    db_cache.set_thermal_grid_cache(_make_thermal("icon-ch1", _INIT, [_INIT]))
    db_cache.save_cache()
    assert calls["thermal"] == 1
    assert db_cache._grid_wind_dirty is False
    assert db_cache._thermal_grid_dirty is False

    # A run that only touches the wind grid must not rewrite the untouched thermal grid.
    db_cache.set_grid_wind_cache(_make_grid_wind("icon-ch2", _INIT, [_INIT + timedelta(hours=34)]))
    db_cache.save_cache()
    assert calls["thermal"] == 1


def test_grid_cache_save_load_round_trip():
    """The grid store's meta array is positional/unversioned — a save->wipe->load must
    reproduce the exact same values, or a future field reorder would misinterpret it silently.
    """
    ch1_vt = [_INIT]
    ch2_vt = [_INIT + timedelta(hours=34)]
    db_cache.set_grid_wind_cache(_make_grid_wind("icon-ch1", _INIT, ch1_vt))
    db_cache.set_grid_wind_cache(_make_grid_wind("icon-ch2", _INIT, ch2_vt))
    db_cache.set_thermal_grid_cache(_make_thermal("icon-ch1", _INIT, ch1_vt))
    db_cache.set_thermal_grid_cache(_make_thermal("icon-ch2", _INIT, ch2_vt))

    before_wind = db_cache.get_grid_wind_cache()
    before_thermal = db_cache.get_thermal_grid_cache()
    db_cache.save_cache()
    assert db_cache.GRID_CACHE_FILE.exists()
    assert db_cache.THERMAL_GRID_CACHE_FILE.exists()

    # Simulate a container restart: wipe all in-memory state, then load from disk.
    db_cache._grid_wind_cache = None
    db_cache._thermal_grid_cache = None
    db_cache._grid_wind_model_init = {}
    db_cache._thermal_grid_model_init = {}
    db_cache.load_cache()

    after_wind = db_cache.get_grid_wind_cache()
    after_thermal = db_cache.get_thermal_grid_cache()

    assert after_wind is not None and after_thermal is not None
    assert len(after_wind.valid_times) == len(before_wind.valid_times)
    assert after_wind.valid_times[0] == before_wind.valid_times[0]
    assert after_wind.valid_times[-1] == before_wind.valid_times[-1]
    np.testing.assert_array_equal(after_wind.ws[500], before_wind.ws[500])
    np.testing.assert_array_equal(after_wind.rh, before_wind.rh)
    np.testing.assert_array_equal(after_thermal.solar, before_thermal.solar)
    assert after_wind.n_lat == before_wind.n_lat
    assert after_wind.step_deg == before_wind.step_deg


# ---------- Station forecast / altitude-winds stitch (CH1/CH2 seam, previous-run backfill) ----------

def _make_hour(valid_time: datetime, wind_speed: float | None = 10.0) -> StationForecastHour:
    """All fields null unless wind_speed is given — mirrors a failed per-horizon fetch,
    which leaves every field on that hour None (see _icon_eps_base.py's per-horizon fetch)."""
    val = None if wind_speed is None else wind_speed
    return StationForecastHour(
        valid_time=valid_time,
        wind_speed=val, wind_speed_min=val, wind_speed_max=val,
        wind_gust=val, wind_gust_min=val, wind_gust_max=val,
        wind_direction=val, wind_direction_min=val, wind_direction_max=val,
        temperature=val, temperature_min=val, temperature_max=val,
        humidity=val, humidity_min=val, humidity_max=val,
        pressure_qff=val, pressure_qff_min=val, pressure_qff_max=val,
        precipitation=val, precipitation_min=val, precipitation_max=val,
    )


def _make_station_forecast(model: str, hours: list[StationForecastHour]) -> StationForecastResponse:
    return StationForecastResponse(
        station_id="test-station", init_time=_INIT, model=model, source="swissmeteo", forecast=hours,
    )


def test_merge_station_forecasts_labels_combined_model():
    ch1 = _make_station_forecast("icon-ch1", [_make_hour(_INIT + timedelta(hours=h)) for h in range(34)])
    ch2 = _make_station_forecast(
        "icon-ch2", [_make_hour(_INIT + timedelta(hours=h)) for h in range(34, 40)]
    )
    db_cache.set_station_forecast("s1", ch1)
    db_cache.set_station_forecast("s1", ch2)

    merged = db_cache.get_station_forecast("s1")
    assert merged.model == "icon-ch1+ch2"
    assert len(merged.forecast) == 40


def test_merge_station_forecasts_keeps_ch1_label_when_ch2_adds_nothing():
    """CH2 present but contributes no tail (fully behind CH1's range) — no merge actually
    happened, so the label must not falsely claim CH2 provenance."""
    ch1 = _make_station_forecast("icon-ch1", [_make_hour(_INIT + timedelta(hours=h)) for h in range(34)])
    ch2 = _make_station_forecast("icon-ch2", [_make_hour(_INIT + timedelta(hours=10))])
    db_cache.set_station_forecast("s1", ch1)
    db_cache.set_station_forecast("s1", ch2)

    merged = db_cache.get_station_forecast("s1")
    assert merged.model == "icon-ch1"


def test_get_station_forecast_backfills_null_hours_from_previous_ch1_run():
    """Reproduces the reported CH1/CH2 stitch bug: a run whose late horizons (h19-33)
    failed to fetch must not serve null when the previous CH1 run has real data for the
    same valid_time — CH2 can't help here since it only covers h34+."""
    old_run = _make_station_forecast(
        "icon-ch1", [_make_hour(_INIT + timedelta(hours=h), wind_speed=5.0 + h) for h in range(34)]
    )
    db_cache.set_station_forecast("s1", old_run)

    new_init = _INIT + timedelta(hours=6)
    new_hours = [
        _make_hour(new_init + timedelta(hours=h), wind_speed=(20.0 + h) if h <= 18 else None)
        for h in range(34)
    ]
    new_run = _make_station_forecast("icon-ch1", new_hours)
    db_cache.set_station_forecast("s1", new_run)

    result = db_cache.get_station_forecast("s1")
    assert all(h.wind_speed is not None for h in result.forecast[:19])
    # h19 of the new run == old run's h25 (both fall on new_init + 19h)
    backfilled = result.forecast[19]
    assert backfilled.valid_time == new_init + timedelta(hours=19)
    assert backfilled.wind_speed == pytest.approx(5.0 + 25)
    # Backfill only reaches as far as the previous run's own horizon (h33, i.e.
    # new_init+27h since runs are 6h apart) — h28-33 have no source anywhere and stay null.
    assert all(h.wind_speed is not None for h in result.forecast[19:28])
    assert all(h.wind_speed is None for h in result.forecast[28:34])


def test_get_station_forecast_no_previous_run_leaves_nulls():
    """No prior CH1 run cached (e.g. first collection since startup) — nulls stay null
    rather than crashing or fabricating data."""
    hours = [_make_hour(_INIT + timedelta(hours=h), wind_speed=None if h >= 19 else 1.0) for h in range(34)]
    db_cache.set_station_forecast("s1", _make_station_forecast("icon-ch1", hours))

    result = db_cache.get_station_forecast("s1")
    assert result.forecast[19].wind_speed is None


def _make_level(level_m: int, wind_speed: float | None) -> AltitudeWindLevel:
    return AltitudeWindLevel(
        level_m=level_m,
        wind_speed=wind_speed, wind_speed_min=wind_speed, wind_speed_max=wind_speed,
        wind_direction=wind_speed, wind_direction_min=wind_speed, wind_direction_max=wind_speed,
        vertical_wind=wind_speed, vertical_wind_min=wind_speed, vertical_wind_max=wind_speed,
    )


def _make_profile(valid_time: datetime, wind_speed: float | None) -> AltitudeWindsProfile:
    return AltitudeWindsProfile(valid_time=valid_time, levels=[_make_level(1000, wind_speed)])


def _make_altitude_winds(model: str, profiles: list[AltitudeWindsProfile]) -> AltitudeWindsResponse:
    return AltitudeWindsResponse(
        station_id="test-station", init_time=_INIT, model=model, source="swissmeteo", profiles=profiles,
    )


def test_get_station_altitude_winds_backfills_null_profiles_from_previous_ch1_run():
    old_run = _make_altitude_winds(
        "icon-ch1", [_make_profile(_INIT + timedelta(hours=h), 5.0 + h) for h in range(34)]
    )
    db_cache.set_station_altitude_winds("s1", old_run)

    new_init = _INIT + timedelta(hours=6)
    new_profiles = [
        _make_profile(new_init + timedelta(hours=h), (20.0 + h) if h <= 18 else None) for h in range(34)
    ]
    db_cache.set_station_altitude_winds("s1", _make_altitude_winds("icon-ch1", new_profiles))

    result = db_cache.get_station_altitude_winds("s1")
    backfilled = result.profiles[19]
    assert backfilled.levels[0].wind_speed == pytest.approx(5.0 + 25)


def test_merge_altitude_winds_labels_combined_model():
    ch1 = _make_altitude_winds("icon-ch1", [_make_profile(_INIT + timedelta(hours=h), 1.0) for h in range(34)])
    ch2 = _make_altitude_winds(
        "icon-ch2", [_make_profile(_INIT + timedelta(hours=h), 1.0) for h in range(34, 40)]
    )
    db_cache.set_station_altitude_winds("s1", ch1)
    db_cache.set_station_altitude_winds("s1", ch2)

    merged = db_cache.get_station_altitude_winds("s1")
    assert merged.model == "icon-ch1+ch2"
    assert len(merged.profiles) == 40
