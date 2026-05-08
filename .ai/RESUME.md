# Resume Notes — 2026-05-08

## What Was Done This Session

### v0.3.1 released and confirmed working on PRD

Tagged and pushed v0.3.1 (all fixes below confirmed working on lsmfapi.lg4.ch).

### README updated to reflect v0.3.0 state

Major corrections: CH2 h34–120 hourly / 4×/day; CH1 10 members dynamic; removed `cloud_base_convective` + `boundary_layer_height` (HBAS_CON/HPBL not in EPS catalog); added altitude-winds and stations endpoints to API overview; corrected architecture (GRIB cache, RH via TD_2M, updated repo layout); scheduler cron times replacing interval config; config table cleaned of non-existent scheduler keys; roadmap restructured. `pyproject.toml` bumped 0.2.0 → 0.3.0.

### CI eccodes version mismatch fixed

`ubuntu-latest` (Ubuntu 24.04) ships `libeccodes 2.34.1` via apt. `eccodes-cosmo-resources-python==2.38.3.1` definition files require ≥2.38 — the COSMO parser failed with a syntax error, all GRIB shortNames came back `<unknown>`, `_read_grid_coords` raised RuntimeError. Fix: removed apt libeccodes install; added Miniforge step to install `eccodes=2.38.3` from conda-forge; set `LD_LIBRARY_PATH=$HOME/miniforge/lib` so Python `findlibs` resolves the 2.38 C library.

### Traefik cross-routing fixed (PRD/DEV on same host)

`docker-compose.yml` was the base for the DEV overlay. Docker Compose merges label lists, so the DEV container inherited all PRD Traefik labels too. Traefik load-balanced between PRD and DEV for `lsmfapi.lg4.ch` — dashboard flipped between "no data" (fresh PRD) and "all ready" (DEV with warm cache). Fix: stripped all Traefik labels from `docker-compose.yml`. PRD compose file lives outside this repo on the XPS server.

### Scheduler warm-up / cron race fixed

Root cause: container started at 19:58Z, warm-up used `ref_dt=12:00Z` (118 min past 18Z, guard `< 2h` = true). At exactly 20:00Z the cron fired; `_latest_ref_dt()` returned `18:00Z` (7200s = not `< 7200`). New `grib_run_dir("ch1", 18:00Z)` called `_purge_stale` which deleted the `20260508T1200Z/` directory mid-download. All in-flight writes (U_10M h=2,3,4,5) failed with `FileNotFoundError`.

Fix: added `_ch1_lock` / `_ch2_lock` asyncio locks in `scheduler.py`. If a collection is already running, the incoming trigger logs a skip and returns immediately. No concurrent same-model runs → no cross-ref_dt purge race.

---

# Resume Notes — 2026-04-30

## Integration test — E2E collection smoke test

Added `tests/test_e2e_collection.py` (`@pytest.mark.integration`). Runs `IconCh1EpsCollector.collect()` with one hardcoded station (Interlaken) and HORIZONS patched to `[0, 6]`. Mocks `_fetch_stations` so CI doesn't need Lenticularis. Asserts cache is populated with non-null, physically plausible wind_speed/temperature/pressure_qff. Takes ~2–4 min.

`.github/workflows/integration-test.yml` runs on every push to `main` and every PR. Local: `pytest -m integration -v`.

`pyproject.toml` — added `[tool.poetry.group.dev.dependencies]` with pytest + pytest-asyncio, plus `[tool.pytest.ini_options]` (asyncio_mode=auto, integration marker).

---

## Bug Fix: CH1/CH2 collections both failing since last deploy

**Root cause**: `HBAS_CON` and `HPBL` were removed from `SURFACE_VARS` in the previous session, but the `surf_array("HBAS_CON")` and `surf_array("HPBL")` calls in both `collect()` methods were not removed. `surf_tasks` is built from `SURFACE_VARS`, so `surf_tasks["HBAS_CON"]` raised `KeyError: 'HBAS_CON'` and aborted every collection run.

**Fix**: Removed the two dead `surf_array()` lines from `icon_ch1_eps.py:735` and `icon_ch2_eps.py:350`. Both variables were already absent from `StationForecastHour` output — they had no consumers.

Error in logs: `Collection failed for ch1: 'HBAS_CON'` / `Collection failed for ch2: 'HBAS_CON'`

---

## What Was Done This Session

### Dashboard errors panel now includes download failures

`telemetry.py` `_recent_errors` deque was only populated from HTTP middleware. Collector failures (STAC search errors, download errors, eccodes parse errors) were tracked as counter deltas (`files_done − files_ok`) but never shown in the errors panel.

**Fix**: Added `record_download_error(model, variable, horizon_h, error_msg)` to `telemetry.py`. Uses the same dict shape as HTTP errors (`ts`, `method`, `path`, `status`, `detail`) with `method=CH1/CH2`, `path="VARNAME h+N"`, `status="DL-ERR"`. The existing `renderErrors()` JS table renders them without any frontend changes.

Both CH1 and CH2 collectors now call `_telemetry.record_download_error()` at all three failure points (STAC search exception, download exception, eccodes parse exception).

### Corrupt GRIB files now self-delete on eccodes failure

`_read_grib2_eccodes()` was catching exceptions internally and returning `(None, None)`. The caller's `except` block (which calls `dest.unlink(missing_ok=True)`) never fired — truncated GRIB files persisted in `/tmp/lsmfapi_grib/` across container restarts causing the same eccodes error every run until `ref_dt` changed.

**Fix**: Changed `return None, None` → `raise` in `_read_grib2_eccodes` exception handler. All 6 call sites were already in `try/except` or `try/finally` with `dest.unlink()` guards — safe to re-raise.

### Silent `url is None` now logs a WARNING

`_search_item_url` returns `None` when STAC returns no features. The caller `_fetch_step` was silently returning `None` with no log and no telemetry. Added `logger.warning()` to both CH1 and CH2 `_fetch_step` when `url is None`.

### HBAS_CON and HPBL removed from SURFACE_VARS

STAC catalog query confirmed: neither `hbas_con` nor `hpbl` is published in `ch.meteoschweiz.ogd-forecasting-icon-ch1` or `ch.meteoschweiz.ogd-forecasting-icon-ch2`. Every run was wasting 68 STAC calls (2 vars × 34 horizons) that always returned `features: []`.

Removed both from `SURFACE_VARS` in `icon_ch1_eps.py`. CH2 imports `SURFACE_VARS` from CH1 so the fix covers both collectors.

**Full CH1/CH2-EPS catalog** (confirmed via STAC search with `forecast:perturbed: true`):
`alb_rad`, `alhfl_s`, `ashfl_s`, `asob_s`, `aswdifd_s`, `aswdifu_s`, `aswdir_s`, `athb_s`, `cape_ml`, `cape_mu`, `ceiling`, `cin_ml`, `cin_mu`, `clc`, `clch`, `clcl`, `clcm`, `clct`, `dbz_850`, `dbz_cmax`, `dursun`, `dursun_m`, `grau_gsp`, `h_snow`, `hzerocl`, `lcl_ml`, `lfc_ml`, `p`, `pmsl`, `ps`, `qc`, `qv`, `rain_gsp`, `sdi_2`, `sli`, `snow_gsp`, `snowlmt`, `t`, `t_2m`, `t_g`, `t_snow`, `t_so`, `td_2m`, `tke`, `tmax_2m`, `tmin_2m`, `tot_pr`, `tot_prec`, `twater`, `u`, `u_10m`, `v`, `v_10m`, `vmax_10m`, `w`, `w_snow`, `z0`

---

# Resume Notes — 2026-04-29

## What Was Done This Session

### Collector horizons & schedule redesign

CH1 and CH2 now together cover the full 120-hour window hourly:

| Model | HORIZONS | Steps | Cron (UTC) | Logic |
|---|---|---|---|---|
| ICON-CH1-EPS | h0–h33 (inclusive) | hourly | 02/08/14/20Z | 2 h after each 00/06/12/18Z release |
| ICON-CH2-EPS | h34–h120 (inclusive) | hourly | 03/09/15/21Z | 3 h after each 00/06/12/18Z release |

CH2 does **not** download h0–h33 (those belong to CH1). CH1 always wins the write for its slice.

### Root cause of all-NULL station values — fixed

`N_MEMBERS` was hardcoded (11 for CH1, 21 for CH2) but MeteoSwiss currently delivers **10 members** for CH1. The shape check `r.shape == (11, n_stations)` failed at every step, so every value fell through to the all-NaN fallback.

**Fix**: `N_MEMBERS` is now labelled `# informational only`. After all fetch tasks complete, the actual count is read from the first valid 2-D result:

```python
_n_members = next(
    (t.result().shape[0] for ts in surf_tasks.values() for t in ts
     if _task_ok(t) and t.result().ndim == 2),
    1,
)
```

`_nan_surf`, `nan_prior`, `nan_pres` are all built from `_n_members`; `_get_prior()` compares against the runtime shape. Log line `"CH1 ensemble members in GRIB: %d"` confirms the detected count at every run.

### GRIB file persistence cache — `collectors/grib_cache.py`

Replaces `tempfile.TemporaryDirectory`. Files persist in `/tmp/lsmfapi_grib/{model}/{YYYYMMDDTHHMMZ}/` across container restarts.

- `grib_run_dir(model, ref_dt)` — context manager: purges stale run dirs on entry, **does not delete** on exit
- `_fetch_step()` cache-hit check: `if dest.exists() and dest.stat().st_size > 1024: skip download`
- Corrupt file guard: eccodes failure → `dest.unlink(missing_ok=True)` → re-downloaded next start
- Old runs (different `ref_dt`) are deleted automatically on next startup

### Dashboard download ok/failed counts

`collection_state.py` now tracks `files_ok` (downloaded + parsed successfully) alongside `files_done` (attempted). `failed = files_done − files_ok`.

- **During a run**: progress bar label shows `"X / Y (Z%) · ⚠ N failed"` in red when N > 0
- **After completion**: model detail card row shows `"N / T ok · ⚠ F failed"` or `"· ✓ all ok"`

Collector `fetch()` closures: `result: np.ndarray | None = None` declared before `try` so `finally` can check success and increment `progress[1]` (ok counter).

---

# Resume Notes — 2026-04-23

## What Was Done This Session

### CH1/CH2 cache merge fix

**Root cause** — `set_station_forecast()` was a plain dict overwrite (`_station_cache[key] = data`). CH2 ran last in `_warm_cache()` and overwrote CH1's entry for every station, discarding 0–30h of hourly data.

**Fix** — `database/cache.py` now uses two separate dicts:
- `_ch1_station_cache` / `_ch2_station_cache`
- `_ch1_altitude_winds_cache` / `_ch2_altitude_winds_cache`

`set_station_forecast()` routes by `data.model` (`"icon-ch1"` → CH1 dict, else → CH2 dict). `get_station_forecast()` merges on the fly: CH1 entries (h0–h30, 1h steps) + CH2 entries where `valid_time > last CH1 valid_time` (first CH2 step served is h33). Each collector refreshes only its own slice — a CH1 re-run doesn't touch the CH2 tail, and vice versa.

`save_cache()` / `load_cache()` use new JSON keys `ch1_station`, `ch2_station`, `ch1_altitude_winds`, `ch2_altitude_winds`. Old `cache.json` files (with key `station`) will start fresh on next container boot.

`station_cache_detail()` now returns `{count, ch1: {...}, ch2: {...}, combined_forecast_hours, init_time, valid_until}` instead of a flat dict. `altitude_winds_cache_detail()` returns `{count, ch1_count, ch2_count}`.

Dashboard JS (`static/dashboard.js`) updated to display CH1 and CH2 cache state separately.

---

# Resume Notes — 2026-04-18

## Status

Both collectors fully operational against live MeteoSwiss data. API serving real forecast data.
Traefik routing working. Cache persistence implemented. Altitude winds moved to separate endpoint.
Web GUI stations loading fixed (CORS proxy added).

## What Was Done This Session

### Pipeline fixes (all landed, confirmed working)

**Asset key bug** — `_search_item_url` was looking for `.get("data")` on the assets dict; MeteoSwiss
uses the filename as the asset key. Fixed to `next(iter(assets.values())).get("href")`.

**QV → TD_2M swap** — QV (specific humidity, 3D field, 80 vertical levels, ~80-100MB per file)
replaced with TD_2M (2m dew point temperature, small surface field, ~5MB). RH now computed via
Magnus formula in `_compute_rh_from_td(t_k, td_k)`. Saves ~30 min per collection run.

**3D shape mismatch** — surf_array() now handles ndim==3 results by taking `r[:, -1, :]`
(bottom model level). Was causing ValueError when any 3D variable slipped into surface processing.

**Progress logging** — counter `n/total` logged every 20 tasks and at completion.

**Diagnostic summary** — after gather: `CH1 surface fetch: X/Y tasks returned data`.

**httpx/httpcore log spam** suppressed in `main.py`.

### Architecture change: pressure_levels separated

`pressure_levels: list[PressureLevelWinds]` removed from `ForecastPoint` / station response.
Altitude winds now stored and served separately:

- New models: `AltitudeWindsPoint`, `AltitudeWindsResponse` (in `models/forecast.py`)
- New cache functions: `get_station_altitude_winds`, `set_station_altitude_winds` (in `database/cache.py`)
- New endpoint: `GET /api/forecast/altitude-winds?station_id=&hours=`
- Both CH1 and CH2 collectors build and cache altitude winds alongside the surface forecast

### Cache persistence

`database/cache.py` now persists the in-memory cache to `/app/data/cache.json`:
- `load_cache()` — called at startup before scheduler; loads stale data so API is immediately usable
- `save_cache()` — atomic write (`.tmp` → rename); called after each successful collection + on graceful shutdown
- Volume mount `./data:/app/data` added to both `docker-compose.yml` and `docker-compose.dev.yml`
- `./data` excluded from rsync in `LSMF-dev.ps1` (intentional — never overwrite remote cache)

### Traefik fixes

- `docker-compose.dev.yml` had `tls=true` but no `certresolver` → self-signed cert error. Added:
  `traefik.http.routers.lsmfapi-dev.tls.certresolver=letsencrypt`
- Added explicit port label: `traefik.http.services.lsmfapi-dev.loadbalancer.server.port=8000`
- Startup was blocking (lifespan awaited full collection before yielding) → Traefik saw container
  as "starting" / unhealthy. Fixed: initial collection now runs as `asyncio.create_task(_warm_cache())`
  so FastAPI starts serving immediately; health checks pass; Traefik routes traffic.

### Web GUI: stations CORS fix

`index.js` was fetching Lenticularis directly from the browser → CORS blocked.
Added `/api/stations` proxy endpoint in `accuracy.py` that calls Lenticularis server-side.
Also fixed field names: `s.station_id`, `s.latitude`, `s.longitude` (was `s.id`, `s.lat`, `s.lon`).

## Known Issues / Deferred

- **`sunshine_minutes` wrong for CH2 first step** — h=30 is the first CH2 horizon; the accumulated
  value from model start (30h of sunshine) is used as the delta, not 3h. Needs special-casing for
  the first step: treat the accumulated value as the per-step value, or fetch h=27 to diff against.
- **`cin: -999.9`** — ICON fill value for "no convection present". Should map to `null`.
  Fix: clip `CIN_ML` to `None` where value < -900.
- **U/V/W pressure-level data all null** — probe downloads succeed but values are null in response.
  Not yet investigated. May be eccodes level-type mismatch or STAC search returning no results.
- **`fetchActuals` / `fetchForecasts` in index.js** still call Lenticularis directly from browser
  → will CORS-fail when analysis is run. Need same proxy treatment as stations.
- **Wind-grid endpoint** (`GET /api/forecast/wind-grid`) is a stub — `set_grid_forecast` is never
  called. Not yet implemented.

## Key Files

```
src/lsmfapi/collectors/icon_ch1_eps.py   — CH1 collector (h0–h33 hourly); all shared helpers live here
src/lsmfapi/collectors/icon_ch2_eps.py   — CH2 collector (h34–h120 hourly); imports helpers from CH1
src/lsmfapi/collectors/grib_cache.py     — grib_run_dir() context manager; persistent GRIB dirs in /tmp
src/lsmfapi/models/forecast.py           — all Pydantic models incl. AltitudeWindsResponse
src/lsmfapi/database/cache.py            — in-memory cache + save/load persistence
src/lsmfapi/database/collection_state.py — runtime collection state (status, files_done, files_ok, errors)
src/lsmfapi/scheduler.py                 — APScheduler + _warm_cache background task
src/lsmfapi/api/routers/forecast.py      — /api/forecast/station + /api/forecast/altitude-winds
src/lsmfapi/api/routers/accuracy.py      — accuracy GUI + /api/stations proxy
src/lsmfapi/api/main.py                  — lifespan: load_cache → scheduler → save_cache on shutdown
static/dashboard.js                      — dashboard frontend; renderCollection() shows ok/failed counts
static/index.js                          — accuracy GUI frontend
docker-compose.yml                       — base compose (data volume mount)
docker-compose.dev.yml                   — dev overlay (Traefik labels, live src mount, data volume)
scripts/LSMF-dev.ps1                     — SSH deploy script (deploy/sync/restart/logs/exec)
scripts/diag_interlaken.py               — dry-run diagnostic: prints raw ensemble values for one station
config.yml                               — meteoswiss URLs, lenticularis base_url, scheduler intervals
```

## Context Files to Read

- `.ai/context/architecture.md` — STAC API, variables, eccodes, altitude mapping, GRIB cache, API contracts
- `.ai/context/features.md` — shipped vs backlog
