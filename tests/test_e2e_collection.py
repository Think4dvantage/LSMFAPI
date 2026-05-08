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

    # Clear forecast caches so assertions are not fooled by a previous run
    db_cache._ch1_station_cache.clear()
    db_cache._ch2_station_cache.clear()

    yield

    db_cache._ch1_station_cache.clear()
    db_cache._ch2_station_cache.clear()


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

    # Reduce download volume: 2 steps × 17 vars ≈ 40 GRIB files (~2–4 min)
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
