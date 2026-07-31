"""End-to-end integration test: CH1 collection pipeline for one station.

Hits the real MeteoSwiss STAC API but mocks Lenticularis and reduces the
horizon range so the test finishes in ~2–4 minutes rather than ~20.

Run:
    pytest -m integration -v
Skip in fast/unit runs:
    pytest -m "not integration"
"""

import pytest
from unittest.mock import AsyncMock

import lsmfapi.collectors.icon_ch1_eps as ch1_mod
from lsmfapi.collectors.icon_ch1_eps import IconCh1EpsCollector
from lsmfapi.database import cache as db_cache

# Hardcoded station avoids Lenticularis dependency in CI
_INTERLAKEN = {
    "station_id": "meteoswiss-INT",
    "name": "Interlaken",
    "latitude": 46.68,
    "longitude": 7.86,
    "elevation": 580,
}

# h=0 covers immediate values; h=6 exercises de-accumulation (6 steps of diff)
_TEST_HORIZONS = [0, 6]


@pytest.fixture(autouse=True)
def reset_state(monkeypatch):
    """Reset all module-level state so tests are isolated."""
    from lsmfapi.config import Config, MeteoSwissConfig, LenticularisConfig

    # Patch config — no config.yml needed on disk
    _cfg = Config(
        meteoswiss=MeteoSwissConfig(),
        lenticularis=LenticularisConfig(base_url="http://unused.example.com"),
    )
    monkeypatch.setattr(ch1_mod, "get_config", lambda: _cfg)

    # Reset CH1 grid singleton so each test builds its own KD-tree
    ch1_mod._GRID_TREE = None
    ch1_mod._GRID_LATS = None
    ch1_mod._GRID_LONS = None
    ch1_mod._GRID_SAMPLE_INDICES = None
    ch1_mod._GRID_N_LAT = 0
    ch1_mod._GRID_N_LON = 0
    ch1_mod._GRID_LEVEL_HEIGHTS = None

    # Clear forecast caches so assertions are not fooled by a previous run
    db_cache._ch1_station_cache.clear()
    db_cache._ch2_station_cache.clear()
    db_cache._ch1_altitude_winds_cache.clear()
    db_cache._ch2_altitude_winds_cache.clear()

    yield

    db_cache._ch1_station_cache.clear()
    db_cache._ch2_station_cache.clear()
    db_cache._ch1_altitude_winds_cache.clear()
    db_cache._ch2_altitude_winds_cache.clear()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ch1_collects_interlaken(monkeypatch):
    """
    Full CH1-EPS collection pipeline: STAC search → GRIB download → eccodes
    parse → ensemble stats → cache population.

    Asserts that wind_speed, temperature, and pressure_qff are non-null and
    within plausible physical ranges for Interlaken.
    """
    from lsmfapi._eccodes import setup_definitions
    setup_definitions()

    # Reduce download volume: 2 steps × ~23 vars GRIB files, plus the static horizontal/
    # vertical constants (the latter ~172 MB, for HHL model-level heights) (~2–4 min)
    monkeypatch.setattr(ch1_mod, "HORIZONS", _TEST_HORIZONS)

    collector = IconCh1EpsCollector()
    # Stub Lenticularis — CI cannot reach the homelab
    monkeypatch.setattr(collector, "_fetch_stations", AsyncMock(return_value=[_INTERLAKEN]))

    await collector.collect()

    result = db_cache.get_station_forecast("meteoswiss-INT")
    assert result is not None, "Forecast cache is empty after CH1 collection"
    assert result.model == "icon-ch1"
    assert len(result.forecast) == len(_TEST_HORIZONS), (
        f"Expected {len(_TEST_HORIZONS)} forecast hours, got {len(result.forecast)}"
    )

    first = result.forecast[0]
    assert first.wind_speed is not None, "wind_speed is None — U_10M/V_10M extraction failed"
    assert first.temperature is not None, "temperature is None — T_2M fetch or conversion failed"
    assert first.pressure_qff is not None, "pressure_qff is None — PMSL fetch failed"

    # Sanity-range checks catch unit/scaling bugs (e.g. m/s vs km/h, K vs °C, Pa vs hPa)
    assert 0 <= first.wind_speed <= 200, f"implausible wind_speed (km/h): {first.wind_speed}"
    assert -40 <= first.temperature <= 50, f"implausible temperature (°C): {first.temperature}"
    assert 800 <= first.pressure_qff <= 1100, f"implausible QFF pressure (hPa): {first.pressure_qff}"

    # Altitude winds must resolve to DISTINCT geometric heights, not collapse onto a single
    # model level. The pre-fix bug mapped all 9 bands to the same array index (the EPS U/V/W
    # files are generalVerticalLayer model levels with no pv, so pressure matching failed),
    # yielding identical or all-null winds. This asserts the HHL height interpolation works.
    aw = db_cache.get_station_altitude_winds("meteoswiss-INT")
    assert aw is not None and aw.profiles, "altitude winds cache empty after collection"
    levels0 = aw.profiles[0].levels
    assert [lvl.level_m for lvl in levels0] == [500, 800, 1000, 1500, 2000, 2500, 3000, 4000, 5000]
    speeds = [lvl.wind_speed for lvl in levels0 if lvl.wind_speed is not None]
    assert len(speeds) >= 2, "altitude winds all null — model-level→height mapping failed"
    assert len({round(s, 1) for s in speeds}) >= 2, (
        "altitude winds identical across bands — levels collapsed onto one "
        "(regression of the generalVerticalLayer index bug)"
    )

    # W is reported on generalVertical half-levels (81), one more than U/V's
    # generalVerticalLayer full levels (80) — sizing W's NaN template off the U probe's
    # level count silently discarded every W array as a shape mismatch, so vertical_wind
    # was null in every response regardless of station or hour.
    vwinds = [lvl.vertical_wind for lvl in levels0 if lvl.vertical_wind is not None]
    assert len(vwinds) >= 1, (
        "vertical_wind all null — W half-level→full-level averaging or its NaN-template "
        "sizing regressed"
    )
