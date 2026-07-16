# Testing Conventions

## Philosophy

Tests exist to catch regressions in the collection pipeline where silent failures (all-null values, wrong units, wrong ensemble stats) are easy to introduce and hard to notice. Coverage of the API surface is secondary.

---

## Integration Tests: Pytest

Use `pytest` + `pytest-asyncio` for all backend tests.

### Location

All tests in `tests/` (flat, no subdirectory split yet).

### Markers

- `@pytest.mark.integration` — hits the real MeteoSwiss STAC API; takes 2–4 minutes. Deselect with `pytest -m "not integration"` for fast runs.

### Standards

- `asyncio_mode = "auto"` — set in `pyproject.toml`, no need for `@pytest.mark.asyncio` on individual tests.
- Use `monkeypatch` to patch module-level globals and config. Don't require `config.yml` on disk — patch `get_config()` to return a constructed `Config` object.
- Mock only external boundaries (Lenticularis station fetch). Never mock MeteoSwiss STAC/CDN — that's the thing being tested.
- Reset module-level globals (`_GRID_TREE`, `_GRID_LATS`, etc.) in `autouse` fixtures to isolate tests.

### Example Integration Test Pattern

```python
@pytest.fixture(autouse=True)
def reset_state(monkeypatch):
    from lsmfapi.config import Config, MeteoSwissConfig, LenticularisConfig
    cfg = Config(
        meteoswiss=MeteoSwissConfig(),
        lenticularis=LenticularisConfig(base_url="http://unused.example.com"),
    )
    monkeypatch.setattr(ch1_mod, "get_config", lambda: cfg)
    ch1_mod._GRID_TREE = None
    # ... reset other globals ...
    yield

@pytest.mark.integration
async def test_ch1_collects_interlaken(monkeypatch):
    from lsmfapi._eccodes import setup_definitions
    setup_definitions()
    monkeypatch.setattr(ch1_mod, "HORIZONS", [0, 6])
    collector = IconCh1EpsCollector()
    monkeypatch.setattr(collector, "_fetch_stations", AsyncMock(return_value=[_INTERLAKEN]))
    await collector.collect()
    result = db_cache.get_station_forecast("meteoswiss-INT")
    assert result is not None
    assert 0 <= result.forecast[0].wind_speed <= 200
```

---

## CI

`.github/workflows/integration-test.yml` runs on push to `main` and on PRs.

**eccodes setup in CI**: none — and none is needed. `poetry install` pulls the `eccodeslib` wheel, which ships the C library, and `findlibs` resolves an installed package ahead of `LD_LIBRARY_PATH` and system paths. Do not add `apt-get install libeccodes-dev`, conda, Miniforge, or `LD_LIBRARY_PATH` to the workflow: a system library can only mismatch the binding, never help.

A conda/Miniforge step existed from v0.3.1 until 2026-07-16 and **never produced a single green run** — `conda install` targets the Miniforge base env where conda/mamba live, and pinning the old eccodes there made the solve unsatisfiable. It failed in ~26s, well before pytest started.

**Judging a run**: a real pass reads `1 passed in ~250s`. Anything finishing under ~60s failed in setup and never executed the test.

**A green run is load-bearing.** This test is the only thing that exercises the eccodes stack against real ICON GRIB before an image ships. While CI was red (2026-05-08 → 2026-07-16) an eccodes mismatch reached PRD and crash-looped it. Do not tag a release on a red integration test.

---

## What Not to Test

- Individual GRIB parsing functions in isolation (too coupled to real file shapes).
- Traefik routing or Docker networking.
- Scheduler timing (cron schedules).
- API endpoints with mocked collectors — the value is in testing the real pipeline end-to-end.
